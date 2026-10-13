#!/usr/bin/env python3
"""Make the oracle's own checks fail, on purpose, one failure mode at a time.

Subtitle: a check nobody has watched fail is not a check.

Seven checks in this project have turned out unable to fail. Every assertion
here is paired with a mutation that must make it fail; a test that only shows
the happy path is exactly the shape that produced those seven.

Run:  .venv/bin/python -m pytest tools/oracle/test_oracle_tools.py -q
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

HERE = Path(__file__).resolve().parent
SNAPSHOT = HERE / "tree_snapshot.py"
MIRROR = HERE / "mirror_install.py"
CAPTURE = HERE / "capture_check.py"


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *args], capture_output=True, text=True,
    )


def populate(root: Path) -> None:
    (root / "sub").mkdir(parents=True)
    (root / "a.txt").write_text("alpha\n")
    (root / "sub" / "b.txt").write_text("beta\n")
    os.symlink("a.txt", root / "link")


# ---------------------------------------------------------------- snapshot


def test_snapshot_unchanged_tree_compares_equal(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    tree.mkdir()
    populate(tree)
    run(str(SNAPSHOT), "snapshot", str(tree), str(tmp_path / "before.json"))
    run(str(SNAPSHOT), "snapshot", str(tree), str(tmp_path / "after.json"))
    result = run(str(SNAPSHOT), "compare", str(tmp_path / "before.json"),
                 str(tmp_path / "after.json"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SA2_TREE_UNCHANGED" in result.stdout


@pytest.mark.parametrize(
    "mutate,expected",
    [
        (lambda t: (t / "new.txt").write_text("x"), "created file: new.txt"),
        (lambda t: (t / "a.txt").write_text("CHANGED"), "modified file: a.txt"),
        (lambda t: (t / "sub" / "b.txt").unlink(), "removed file: sub/b.txt"),
        (lambda t: (t / "fresh").mkdir(), "created dir: fresh"),
        (lambda t: _relink(t), "modified symlink: link"),
    ],
    ids=["create", "modify", "delete", "mkdir", "relink"],
)
def test_snapshot_detects_every_kind_of_write(tmp_path: Path, mutate, expected) -> None:
    tree = tmp_path / "tree"
    tree.mkdir()
    populate(tree)
    run(str(SNAPSHOT), "snapshot", str(tree), str(tmp_path / "before.json"))
    mutate(tree)
    run(str(SNAPSHOT), "snapshot", str(tree), str(tmp_path / "after.json"))
    result = run(str(SNAPSHOT), "compare", str(tmp_path / "before.json"),
                 str(tmp_path / "after.json"))
    assert result.returncode == 1, "the comparison passed a tree that was written"
    assert "SA2_TREE_CHANGED" in result.stdout
    assert expected in result.stdout, result.stdout


def _relink(tree: Path) -> None:
    (tree / "link").unlink()
    os.symlink("sub/b.txt", tree / "link")


def test_snapshot_restores_bytes_are_still_caught_by_content_not_mtime(
    tmp_path: Path,
) -> None:
    """A write that is undone is unchanged; a write that is *reverted to other
    bytes of the same length* is not. The predecessor restored one file's bytes
    and called the tree restored."""
    tree = tmp_path / "tree"
    tree.mkdir()
    populate(tree)
    run(str(SNAPSHOT), "snapshot", str(tree), str(tmp_path / "before.json"))
    original = (tree / "a.txt").read_bytes()
    stat = (tree / "a.txt").stat()
    (tree / "a.txt").write_bytes(b"ALPHA\n")           # same length, same mtime below
    os.utime(tree / "a.txt", ns=(stat.st_atime_ns, stat.st_mtime_ns))
    run(str(SNAPSHOT), "snapshot", str(tree), str(tmp_path / "after.json"))
    result = run(str(SNAPSHOT), "compare", str(tmp_path / "before.json"),
                 str(tmp_path / "after.json"))
    assert result.returncode == 1, "a same-size, same-mtime overwrite went unnoticed"
    # and putting the bytes back really is unchanged
    (tree / "a.txt").write_bytes(original)
    run(str(SNAPSHOT), "snapshot", str(tree), str(tmp_path / "after2.json"))
    ok = run(str(SNAPSHOT), "compare", str(tmp_path / "before.json"),
             str(tmp_path / "after2.json"))
    assert ok.returncode == 0, ok.stdout


def test_snapshot_refuses_an_empty_tree(tmp_path: Path) -> None:
    """Two empty snapshots compare equal, which is a check that cannot fail."""
    empty = tmp_path / "empty"
    empty.mkdir()
    result = run(str(SNAPSHOT), "snapshot", str(empty), str(tmp_path / "e.json"))
    assert result.returncode != 0
    assert "empty tree" in result.stderr


def test_snapshot_refuses_to_compare_different_roots(tmp_path: Path) -> None:
    for name in ("one", "two"):
        tree = tmp_path / name
        tree.mkdir()
        (tree / "a.txt").write_text("same\n")
        run(str(SNAPSHOT), "snapshot", str(tree), str(tmp_path / f"{name}.json"))
    result = run(str(SNAPSHOT), "compare", str(tmp_path / "one.json"),
                 str(tmp_path / "two.json"))
    assert result.returncode != 0
    assert "different roots" in result.stderr


def test_snapshot_refuses_a_hand_emptied_snapshot(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    tree.mkdir()
    populate(tree)
    run(str(SNAPSHOT), "snapshot", str(tree), str(tmp_path / "before.json"))
    hollow = json.loads((tmp_path / "before.json").read_text())
    hollow["files"] = {}
    hollow["symlinks"] = {}
    (tmp_path / "hollow.json").write_text(json.dumps(hollow))
    result = run(str(SNAPSHOT), "compare", str(tmp_path / "hollow.json"),
                 str(tmp_path / "before.json"))
    assert result.returncode != 0
    assert "vacuous" in result.stderr


# ------------------------------------------------------------------ mirror


def fake_install(root: Path, saves: bool = True) -> Path:
    """An installation's shape: the executables, the loader's profile index,
    the game's configuration, and `resource` with a shared directory, a large
    and a small shipped file, a relative symlink and, unless `saves` is false,
    the save directory holding one save."""
    install = root / "install"
    (install / "mods" / ".modloader" / "profiles").mkdir(parents=True)
    (install / "Config").mkdir()
    (install / "resource" / "gd_PC").mkdir(parents=True)
    (install / "resource" / "Shader").mkdir()
    (install / "resource" / "Shader" / "a.fx").write_text("shader\n")
    (install / "sonic2app-unpacked.exe").write_bytes(b"MZ" + b"\0" * 64)
    (install / "SA2ModLoader.dll").write_bytes(b"MZ" + b"\0" * 64)
    (install / "mods" / ".modloader" / "profiles" / "Profiles.json").write_text("{}")
    (install / "Config" / "UserConfig.cfg").write_text("<Configs/>")
    (install / "resource" / "gd_PC" / "big.pak").write_bytes(b"\0" * (3 * 1024 * 1024))
    (install / "resource" / "gd_PC" / "small.prs").write_bytes(b"prs")
    os.symlink("small.prs", install / "resource" / "gd_PC" / "alias.prs")
    if saves:
        (install / "resource" / "gd_PC" / "SAVEDATA").mkdir()
        (install / "resource" / "gd_PC" / "SAVEDATA" / "SONIC2B__ALF").write_bytes(b"chao")
    (install / "huge.exe").write_bytes(b"\0" * (3 * 1024 * 1024))
    return install


def mirror_module():
    sys.path.insert(0, str(HERE))
    import mirror_install
    return mirror_install


def test_mirror_shares_bulk_and_copies_writable_files(tmp_path: Path) -> None:
    install = fake_install(tmp_path)
    mirror = tmp_path / "mirror"
    result = run(str(MIRROR), str(install), str(mirror))
    assert result.returncode == 0, result.stderr
    assert (mirror / "huge.exe").is_symlink()
    assert not (mirror / "mods" / ".modloader" / "profiles" / "Profiles.json").is_symlink()
    assert not (mirror / "Config" / "UserConfig.cfg").is_symlink()
    # resource: real directories down to the save directory, shared everywhere else
    gd = mirror / "resource" / "gd_PC"
    for d in (mirror / "resource", gd, gd / "SAVEDATA"):
        assert d.is_dir() and not d.is_symlink(), d
    assert (mirror / "resource" / "Shader").is_symlink(), "shipped data was copied, not shared"
    assert (gd / "big.pak").is_symlink() and (gd / "small.prs").is_symlink()
    assert os.readlink(gd / "alias.prs") == "small.prs", "a relative symlink was not kept as one"
    assert not (gd / "SAVEDATA" / "SONIC2B__ALF").is_symlink()
    stats = json.loads(result.stdout.split("\n", 1)[1])
    # huge.exe at the root, and big.pak and small.prs along the path (N6)
    assert stats["bytes_symlinked"] == 2 * 3 * 1024 * 1024 + 3
    assert mirror_module().save_dir_is_private(mirror)


def test_the_mirror_s_save_directory_is_its_own_and_reset_puts_the_install_s_back(
        tmp_path: Path) -> None:
    """A save the game writes stays in the mirror, and every run starts from
    the installation's saves: bytes, modes and times, the directory's too."""
    M = mirror_module()
    install = fake_install(tmp_path)
    mirror = tmp_path / "mirror"
    M.build(install, mirror, M.DEFAULT_SYMLINK_THRESHOLD, force=False)
    saves, mine = install.joinpath(*M.SAVE_DIR), mirror.joinpath(*M.SAVE_DIR)
    os.utime(saves / "SONIC2B__ALF", ns=(1_000_000_123, 1_000_000_456))
    M.reset_save_dir(install, mirror)
    run(str(SNAPSHOT), "snapshot", str(install), str(tmp_path / "before.json"))
    (mine / "SONIC2B__S01").write_bytes(b"")                  # a new save
    (mine / "SONIC2B__ALF").write_bytes(b"CHAO")              # a save changed in place
    (mine / "sub").mkdir()
    run(str(SNAPSHOT), "snapshot", str(install), str(tmp_path / "after.json"))
    result = run(str(SNAPSHOT), "compare", str(tmp_path / "before.json"),
                 str(tmp_path / "after.json"))
    assert result.returncode == 0, "a save in the mirror reached the installation:\n" + result.stdout
    assert M.save_path_problems(install, mirror) == []
    assert M.saves_of(mine) == {"SONIC2B__ALF": M._file_sha256(mine / "SONIC2B__ALF"),
                                "SONIC2B__S01": M._file_sha256(mine / "SONIC2B__S01"),
                                "sub": "<dir>"}
    assert M.reset_save_dir(install, mirror) == ["created SONIC2B__S01", "created sub",
                                                 "modified SONIC2B__ALF"]
    assert M.reset_save_dir(install, mirror) == []
    (mine / "SONIC2B__ALF").unlink()
    assert M.reset_save_dir(install, mirror) == ["removed SONIC2B__ALF"]
    (mine / "SONIC2B__ALF").chmod(0o600)
    assert M.reset_save_dir(install, mirror) == ["re-permissioned SONIC2B__ALF"]
    for name in ("SONIC2B__ALF",):
        a, b = (saves / name).stat(), (mine / name).stat()
        assert ((mine / name).read_bytes(), b.st_mode, b.st_mtime_ns) == (
            (saves / name).read_bytes(), a.st_mode, a.st_mtime_ns)
    assert sorted(p.name for p in mine.iterdir()) == ["SONIC2B__ALF"]
    assert mine.stat().st_mode == saves.stat().st_mode


def test_a_write_through_a_shared_file_on_the_save_path_still_reaches_the_installation(
        tmp_path: Path) -> None:
    """The negative control for the test above: along the path to the save
    directory everything but it is shared, so the snapshot still watches."""
    M = mirror_module()
    install = fake_install(tmp_path)
    mirror = tmp_path / "mirror"
    M.build(install, mirror, M.DEFAULT_SYMLINK_THRESHOLD, force=False)
    run(str(SNAPSHOT), "snapshot", str(install), str(tmp_path / "before.json"))
    with (mirror / "resource" / "gd_PC" / "small.prs").open("r+b") as stream:
        stream.write(b"XX")
    run(str(SNAPSHOT), "snapshot", str(install), str(tmp_path / "after.json"))
    result = run(str(SNAPSHOT), "compare", str(tmp_path / "before.json"),
                 str(tmp_path / "after.json"))
    assert result.returncode == 1
    assert "modified file: resource/gd_PC/small.prs" in result.stdout


def test_every_write_along_the_save_path_is_reported(tmp_path: Path) -> None:
    """What neither the installation's snapshot nor the reset can see: an
    entry created, removed or replaced in the mirror's own directories on the
    way to its save directory (the review of 2026-09-23, W2)."""
    M = mirror_module()
    install = fake_install(tmp_path)
    mirror = tmp_path / "mirror"
    M.build(install, mirror, M.DEFAULT_SYMLINK_THRESHOLD, force=False)
    assert M.save_path_problems(install, mirror) == []
    gd = mirror / "resource" / "gd_PC"
    (mirror / "resource" / "NEWTOP").write_text("x")
    (gd / "NEWFILE").write_text("x")
    (gd / "small.prs").unlink()
    (gd / "big.pak").unlink()
    (gd / "big.pak").write_bytes(b"written over the link")
    os.unlink(gd / "alias.prs")
    os.symlink("big.pak", gd / "alias.prs")
    assert M.save_path_problems(install, mirror) == [
        "created resource/NEWTOP", "created resource/gd_PC/NEWFILE",
        "removed resource/gd_PC/small.prs", "replaced resource/gd_PC/alias.prs",
        "replaced resource/gd_PC/big.pak"]
    assert (gd / "big.pak").read_bytes() == b"written over the link"
    M.reset_save_dir(install, mirror)                    # the reset does not see them
    assert len(M.save_path_problems(install, mirror)) == 5
    # an installation that gained an entry leaves the mirror stale the same way
    fresh = tmp_path / "fresh"
    M.build(install, fresh, M.DEFAULT_SYMLINK_THRESHOLD, force=False)
    (install / "resource" / "gd_PC" / "patch.prs").write_bytes(b"new")
    assert M.save_path_problems(install, fresh) == ["removed resource/gd_PC/patch.prs"]


def test_an_installation_without_its_own_save_directory_is_refused_for_the_runner(
        tmp_path: Path) -> None:
    """Without a real save directory the mirror shares `resource` whole, the
    runner would rebuild on every call and the reset refuse each time; so the
    runner's build refuses it, naming what is missing (N5). The tool alone
    still builds the old layout."""
    M = mirror_module()
    install = fake_install(tmp_path, saves=False)
    with pytest.raises(SystemExit) as e:
        M.build(install, tmp_path / "m1", M.DEFAULT_SYMLINK_THRESHOLD, force=False,
                require_save_dir=True)
    assert "the installation has no directory resource/gd_PC/SAVEDATA" in str(e.value.code)
    assert not (tmp_path / "m1").exists()
    M.build(install, tmp_path / "m2", M.DEFAULT_SYMLINK_THRESHOLD, force=False)
    assert (tmp_path / "m2" / "resource").is_symlink()
    assert not M.save_dir_is_private(tmp_path / "m2")
    with pytest.raises(SystemExit) as e:
        M.reset_save_dir(install, tmp_path / "m2")
    assert "is shared with the installation" in str(e.value.code)
    # lower case, or a symlink on the path, is not the game's directory either
    (install / "resource" / "gd_PC" / "savedata").mkdir()
    assert M.save_dir_problem(install) == "the installation has no directory " \
        "resource/gd_PC/SAVEDATA"
    other = tmp_path / "elsewhere"
    other.mkdir()
    os.symlink(other, install / "resource" / "gd_PC" / "SAVEDATA")
    assert M.save_dir_problem(install) == "the installation's resource/gd_PC/SAVEDATA is a symlink"


def test_a_save_directory_holding_anything_but_files_is_refused(tmp_path: Path) -> None:
    """A symlink in the installation's save directory would be copied as one,
    and a run's write through it would reach what it names (N1)."""
    M = mirror_module()
    install = fake_install(tmp_path)
    os.symlink("../small.prs", install.joinpath(*M.SAVE_DIR) / "SONIC2B__S02")
    with pytest.raises(SystemExit) as e:
        M.build(install, tmp_path / "m", M.DEFAULT_SYMLINK_THRESHOLD, force=False)
    assert "SONIC2B__S02 is not a regular file" in str(e.value.code)
    assert not (tmp_path / "m").exists()


def test_mirror_write_does_not_reach_the_installation(tmp_path: Path) -> None:
    """The point of the mirror, stated as a test that fails if it stops being
    true: writing every writable-directory file in the mirror leaves the
    installation's snapshot identical."""
    install = fake_install(tmp_path)
    mirror = tmp_path / "mirror"
    run(str(MIRROR), str(install), str(mirror))
    run(str(SNAPSHOT), "snapshot", str(install), str(tmp_path / "before.json"))
    (mirror / "mods" / "probe.csv").write_text("tick,value\n1,2\n")
    (mirror / "mods" / ".modloader" / "profiles" / "Profiles.json").write_text('{"x":1}')
    (mirror / "Config" / "UserConfig.cfg").write_text("<Configs Changed='1'/>")
    (mirror / "root-output.csv").write_text("x\n")
    run(str(SNAPSHOT), "snapshot", str(install), str(tmp_path / "after.json"))
    result = run(str(SNAPSHOT), "compare", str(tmp_path / "before.json"),
                 str(tmp_path / "after.json"))
    assert result.returncode == 0, "a write to the mirror reached the installation:\n" + result.stdout


def test_a_write_through_a_symlinked_file_does_reach_the_installation(
    tmp_path: Path,
) -> None:
    """The negative control for the test above. The mirror shares large files
    by symlink, so writing *through* one is visible in the installation. If
    this ever stops failing, the snapshot has stopped watching."""
    install = fake_install(tmp_path)
    mirror = tmp_path / "mirror"
    run(str(MIRROR), str(install), str(mirror))
    run(str(SNAPSHOT), "snapshot", str(install), str(tmp_path / "before.json"))
    with (mirror / "huge.exe").open("r+b") as stream:   # follows the symlink
        stream.write(b"XX")
    run(str(SNAPSHOT), "snapshot", str(install), str(tmp_path / "after.json"))
    result = run(str(SNAPSHOT), "compare", str(tmp_path / "before.json"),
                 str(tmp_path / "after.json"))
    assert result.returncode == 1, "the snapshot missed a write through a shared file"
    assert "modified file: huge.exe" in result.stdout


def test_mirror_refuses_a_directory_that_is_not_an_installation(tmp_path: Path) -> None:
    (tmp_path / "nope").mkdir()
    result = run(str(MIRROR), str(tmp_path / "nope"), str(tmp_path / "m"))
    assert result.returncode != 0
    assert "not an SA2 installation" in result.stderr


def test_mirror_refuses_to_replace_a_directory_it_did_not_build(tmp_path: Path) -> None:
    install = fake_install(tmp_path)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "precious.txt").write_text("do not delete me\n")
    result = run(str(MIRROR), str(install), str(foreign), "--force")
    assert result.returncode != 0
    assert (foreign / "precious.txt").is_file()


# ---------------------------------------------------------------- captures


def test_capture_check_rejects_an_empty_capture(tmp_path: Path) -> None:
    empty = tmp_path / "empty.csv"
    empty.write_text("tick,value\n")            # header only, no rows
    result = run(str(CAPTURE), str(empty))
    assert result.returncode != 0
    assert "no data rows" in (result.stdout + result.stderr)


def test_capture_check_accepts_a_real_capture_and_counts_its_rows(tmp_path: Path) -> None:
    good = tmp_path / "good.csv"
    good.write_text("tick,value\n1,2\n2,3\n3,4\n")
    result = run(str(CAPTURE), str(good))
    assert result.returncode == 0, result.stderr
    assert "rows=3" in result.stdout
    assert "columns=2" in result.stdout


def test_capture_check_names_a_binary_capture_instead_of_crashing(tmp_path: Path) -> None:
    snap = tmp_path / "sa2-snapshots.bin"
    snap.write_bytes(b"SA2B\x90\x0b\x00\x00" + bytes(range(256)))
    result = run(str(CAPTURE), str(snap))
    assert result.returncode != 0
    out = result.stdout + result.stderr
    assert "--binary-capture" in out and "Traceback" not in out


def test_capture_check_rejects_a_ragged_capture(tmp_path: Path) -> None:
    ragged = tmp_path / "ragged.csv"
    ragged.write_text("tick,value\n1,2\n3\n")
    result = run(str(CAPTURE), str(ragged))
    assert result.returncode != 0
    assert "row width" in (result.stdout + result.stderr)


def test_capture_check_rejects_a_constant_column_stream(tmp_path: Path) -> None:
    """A capture whose every row is identical is what a hook that fired once
    and a hook that never fired both look like."""
    flat = tmp_path / "flat.csv"
    flat.write_text("tick,value\n1,2\n1,2\n1,2\n")
    result = run(str(CAPTURE), str(flat), "--require-varying", "tick")
    assert result.returncode != 0
    assert "never varies" in (result.stdout + result.stderr)


def test_capture_check_flags_a_row_torn_by_the_timeout_kill(tmp_path: Path) -> None:
    """The failure four of the predecessor's captures have and nothing looked
    for: `timeout` kills the game mid-`fprintf` and the last row is a prefix."""
    torn = tmp_path / "torn.csv"
    torn.write_text("tick,a,b,c\n1,1,1,1\n2,2,2,2\n3,3\n")
    result = run(str(CAPTURE), str(torn))
    assert result.returncode != 0
    assert "trailing row is torn" in (result.stdout + result.stderr)


def test_capture_check_allows_the_formats_own_short_rows(tmp_path: Path) -> None:
    """`sa2_native_trace.c` writes `tick,player,entity` when the entity
    pointer is null; that is not raggedness and must not be reported as it."""
    short = tmp_path / "short.csv"
    short.write_text("tick,a,b,c\n1,0,0\n2,2,2,2\n3,0,0\n4,4,4,4\n")
    result = run(str(CAPTURE), str(short), "--short-row-width", "3")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "widths=[3, 4]" in result.stdout


def test_short_row_width_does_not_excuse_a_torn_row(tmp_path: Path) -> None:
    """The negative control for the test above: --short-row-width must not
    become a way to make the torn-row check stop being able to fail."""
    both = tmp_path / "both.csv"
    both.write_text("tick,a,b,c\n1,0,0\n2,2,2,2\n3,3\n")
    result = run(str(CAPTURE), str(both), "--short-row-width", "3")
    assert result.returncode != 0
    assert "trailing row is torn" in (result.stdout + result.stderr)


def test_capture_check_on_a_real_predecessor_capture_that_is_torn() -> None:
    """Not a fixture: the actual file on disk. If this ever passes, either the
    capture was repaired or the check stopped working."""
    real = Path(
        "/workspace/OpenSA2/private/predecessor-native-run/"
        "cityescape-native-trace.csv"
    )
    if not real.is_file():
        pytest.skip(f"predecessor capture not present: {real}")
    result = run(str(CAPTURE), str(real), "--short-row-width", "3")
    assert result.returncode != 0
    assert "trailing row is torn" in (result.stdout + result.stderr)


def test_capture_check_on_a_real_predecessor_capture_that_is_whole() -> None:
    """The companion that stops the test above passing for the wrong reason."""
    real = Path(
        "/workspace/OpenSA2/private/predecessor-native-run/"
        "metalharbor-native-trace.csv"
    )
    if not real.is_file():
        pytest.skip(f"predecessor capture not present: {real}")
    result = run(str(CAPTURE), str(real), "--short-row-width", "3")
    assert result.returncode == 0, result.stdout + result.stderr


# ------------------------------------------------------------ probe exports

INSTALL_DLLS = Path(__file__).resolve().parents[2] / "private/install"


def exports_module():
    sys.path.insert(0, str(HERE))
    import run_oracle_probe
    return run_oracle_probe


PROBE_BUILDS = Path(__file__).resolve().parents[2] / "private/generated/oracle/probe-build"


def test_export_check_is_not_fooled_by_a_dll_that_only_mentions_init() -> None:
    """The Mod Loader DLL exports no `Init`, and `objdump -p` still prints the
    substring, through SizeOfInitializedData and an imported
    InitializeCriticalSection. The old check passed it; the new one must not."""
    dll = INSTALL_DLLS / "SA2ModLoader.dll"
    if not dll.is_file():
        pytest.skip(f"installation DLL not present: {dll}")
    dump = subprocess.run(["objdump", "-p", str(dll)], capture_output=True, text=True).stdout
    assert "Init" in dump, "the hazard this test pins is gone; rethink the test"
    probe = exports_module()
    assert "Init" not in probe.dll_exports(dll)
    assert probe.export_problems(dll), "a DLL with no SA2ModInfo is not a mod"


def test_export_check_accepts_a_real_probe_without_init() -> None:
    """The loader treats Init as optional, and the input-replay probe has none.
    Requiring it -- the first version of the fix did -- refuses a real probe."""
    dll = PROBE_BUILDS / "input_replay" / "input_replay.dll"
    if not dll.is_file():
        pytest.skip(f"probe build not present: {dll}")
    probe = exports_module()
    assert "Init" not in probe.dll_exports(dll)
    assert probe.export_problems(dll) == []


def test_export_check_refuses_a_mod_with_no_callback(tmp_path: Path) -> None:
    """SA2ModInfo alone loads and records nothing."""
    probe = exports_module()
    real = probe.dll_exports
    try:
        probe.dll_exports = lambda dll: {"SA2ModInfo"}
        assert any("callbacks" in p for p in probe.export_problems(tmp_path / "x.dll"))
        probe.dll_exports = lambda dll: {"SA2ModInfo", "OnControl"}
        assert probe.export_problems(tmp_path / "x.dll") == []
    finally:
        probe.dll_exports = real


def test_export_check_reads_real_export_names() -> None:
    """The companion: an exact-name reader that returned nothing would pass the
    test above. steam_api.dll exports SteamAPI_Init, and not Init."""
    dll = INSTALL_DLLS / "steam_api.dll"
    if not dll.is_file():
        pytest.skip(f"installation DLL not present: {dll}")
    names = exports_module().dll_exports(dll)
    assert "SteamAPI_Init" in names
    assert "Init" not in names


def test_a_capture_policy_replaces_the_checks_for_its_capture_only() -> None:
    probe = exports_module()
    got = probe.capture_policies(["imp.csv=251:calls"], ["tick.csv", "imp.csv"])
    assert got == {"imp.csv": (251, ["calls"])}
    assert probe.capture_policies(["imp.csv=3"], ["imp.csv"]) == {"imp.csv": (3, [])}


@pytest.mark.parametrize("spec", ["imp.csv", "imp.csv=", "imp.csv=0", "imp.csv=x:calls",
                                  "=5", "other.csv=5"])
def test_a_capture_policy_that_would_check_nothing_is_refused(spec: str) -> None:
    """A policy for a file nobody collects, or with no row floor, checks nothing."""
    probe = exports_module()
    with pytest.raises(SystemExit):
        probe.capture_policies([spec], ["imp.csv"])
    with pytest.raises(SystemExit):
        probe.capture_policies(["imp.csv=1", "imp.csv=2"], ["imp.csv"])


DEVICES = '''I: Bus=0003 Vendor=046d Product=c52b Version=0111
N: Name="Logitech USB Receiver Keyboard"
H: Handlers=sysrq kbd leds event9
B: KEY=1f 7ffffffffffffff ffffffffffffffff fffffffffffffffe

I: Bus=0003 Vendor=045e Product=028e Version=0114
N: Name="Microsoft X-Box 360 pad"
H: Handlers=event25
B: KEY=7cdb000000000000 0 0 0 0

I: Bus=0003 Vendor=0079 Product=0006 Version=0110
N: Name="Generic USB Joystick"
H: Handlers=event26 js0
B: KEY=0
'''


def test_the_receipt_names_the_hosts_game_controllers() -> None:
    """A pad by its buttons (BTN_GAMEPAD's range, word 4 bit 48 onward), a
    joystick by its joydev handler, and a keyboard neither: a keyboard's KEY
    bitmap is dense below 0x120 and must not be read as a controller."""
    probe = exports_module()
    assert probe.host_game_controllers(DEVICES) == ["Microsoft X-Box 360 pad",
                                                    "Generic USB Joystick"]
    assert probe.host_game_controllers(DEVICES.split("\n\n")[0]) == []


def test_the_staged_profile_turns_every_game_patch_off_but_the_limiter() -> None:
    """SA2ModLoader/config.cpp reads each patch as `json_patches.value(key,
    true)` once the Patches object exists, so a key left out is a patch turned
    on. `"Patches": {}`, which the runner wrote until 2026-09-22, reads as all
    eight on; that is the second assertion, read the loader's way.

    FramerateLimiter is the one on, since 2026-09-23: it is the loader's own
    frame limiter, a floor of 1/60 s per frame and no patch of the game's code,
    and with StretchToWindow on (no child window, so the user can see to play)
    it is what paces the game (the vanilla decision's addendum, adopted by the
    user on 2026-09-24 as option A)."""
    probe = exports_module()
    doc = probe.profile_for(["m"], (640, 480), False, False, 1, 1)
    assert all(doc["Patches"].get(k, True) is (k == "FramerateLimiter")
               for k in probe.GAME_PATCHES)
    assert doc["Graphics"]["StretchToWindow"] is True
    assert all({}.get(k, True) is True for k in probe.GAME_PATCHES)
    assert doc["Graphics"]["EnablePauseOnInactive"] is False
    assert len(probe.GAME_PATCHES) == 8


RECORDING = Path("/workspace/OpenSA2/private/generated/oracle/captures/"
                 "mh-record-20260923-212428/receipt.json")


def test_the_profile_is_the_one_the_user_s_recording_ran_with() -> None:
    """Its digest is part of every capture's oracle mode, and a replay whose
    mode differs from its recording's is refused (oracle_mode config_of). The
    user adopted this profile on 2026-09-24 (option A of the vanilla
    decision's addendum), the one mh-record-20260923-212428 ran with; that
    recording is superseded by the re-record the same ruling asked for, which
    runs with it too. Point RECORDING at the re-record once it exists: a
    change here, a comment aside, would strand it."""
    if not RECORDING.is_file():
        pytest.skip(f"the user's recording is not on this machine: {RECORDING}")
    inputs = json.loads(RECORDING.read_text())["inputs"]
    doc = exports_module().profile_for(inputs["enabled_mods"], (640, 480), False, False, 1, 1)
    assert doc == inputs["modloader_profile"]


def test_the_limiter_is_on_exactly_when_the_vanilla_probe_keeps_its_wrapper() -> None:
    """The limiter runs from the loader's BeginScene wrapper (direct3d.cpp,
    BeginScene_r). With the wrapper restored the limiter never runs and the
    game is unpaced; kept with the limiter off, the wrapper is a range kept
    for nothing. The probe's default KEEP_MASK must also cover every KEEP
    row, since tools/oracle/oracle_mode.py keep_table reads the table whole
    (NIT 7 of the 2026-09-23 review).

    BeginScene is the only range kept: the user adopted option A of the
    vanilla decision's addendum on 2026-09-24, which restores the Present
    wrapper (0x00867AE0), measured unneeded
    (private/generated/s4/addendum-native/RESULTS.md, steps 2b and 2c)."""
    import re
    sys.path.insert(0, str(HERE))
    import oracle_mode as OM
    probe = exports_module()
    doc = probe.profile_for(["m"], (640, 480), False, False, 1, 1)
    keep = OM.keep_table()
    assert keep == {(0x00867B10, 5)}
    assert doc["Patches"]["FramerateLimiter"] is ((0x00867B10, 5) in keep)
    source = (HERE.parent.parent / OM.VANILLA_PROBE).read_text()
    mask = re.search(r"#ifndef KEEP_MASK\s*\n\s*#define KEEP_MASK\s+(0x[0-9A-Fa-f]+)u?", source)
    assert mask and int(mask.group(1), 16) == (1 << len(keep)) - 1, \
        "the default KEEP_MASK leaves a KEEP row out, and keep_table would still count it"


def test_a_probe_that_exports_a_render_event_is_refused(tmp_path: Path) -> None:
    """The kept BeginScene wrapper raises OnRenderSceneStart in every frame."""
    probe = exports_module()
    real = probe.dll_exports
    try:
        for event in probe.RENDER_EVENTS:
            probe.dll_exports = lambda dll, e=event: {"SA2ModInfo", "OnFrame", e}
            assert probe.export_problems(tmp_path / "x.dll") == [
                f"exports {event}, a render event the Mod Loader raises from its Direct3D "
                "wrappers; no probe may run there"]
        probe.dll_exports = lambda dll: {"SA2ModInfo", "OnFrame"}
        assert probe.export_problems(tmp_path / "x.dll") == []
    finally:
        probe.dll_exports = real
    assert probe.RENDER_EVENTS == ("OnRenderSceneStart", "OnRenderSceneEnd",
                                   "OnRenderDeviceLost", "OnRenderDeviceReset")


XRANDR = """Screen 0: minimum 16 x 16, current 3840 x 2160, maximum 32767 x 32767
DP-1 connected primary 3840x2160+0+0 (normal left inverted right x axis y axis) 700mm x 400mm
   3840x2160    240.00*+
   2048x1536    239.85
HDMI-1 disconnected (normal left inverted right x axis y axis)
   1920x1080     60.00 +
DP-2 connected 1920x1080+3840+0 (normal left inverted right x axis y axis) 530mm x 300mm
   1920x1080     59.94 +  60.00*
"""


def test_the_receipt_names_each_display_s_current_mode() -> None:
    probe = exports_module()
    assert probe.host_display_modes(XRANDR) == ["DP-1 3840x2160 240.00", "DP-2 1920x1080 60.00"]
    assert probe.host_display_modes("Screen 0: minimum 16 x 16\n") == [
        "<xrandr listed no current mode>"]


def test_the_receipt_says_which_environment_the_game_inherited_and_no_value() -> None:
    """Native heap addresses followed the environment a run was launched from
    (agents-journals/s4/2026-09-23-recording-session.md), and an agent's
    environment carries tokens: names, count, size and digest, never a value."""
    probe = exports_module()
    env = {"PATH": "/a:/b::/c", "SECRET_TOKEN": "hunter2", "HOME": "/home/x"}
    got = probe.environment_record(env)
    assert got == {"names": ["HOME", "PATH", "SECRET_TOKEN"], "count": 3,
                   "bytes": len("HOME=/home/x\0PATH=/a:/b::/c\0SECRET_TOKEN=hunter2\0"),
                   "sha256": hashlib.sha256(
                       b"HOME=/home/x\0PATH=/a:/b::/c\0SECRET_TOKEN=hunter2\0").hexdigest(),
                   "path_entries": 3}
    assert "hunter2" not in json.dumps(got)
    grown = probe.environment_record({**env, "PATH": "/v/bin:" + env["PATH"]})
    assert (grown["bytes"] - got["bytes"], grown["path_entries"]) == (7, 4)


def test_the_game_s_environment_is_the_session_s_own_and_a_fixed_path() -> None:
    """Two callers of one login session, a terminal that sourced tools/env.sh
    twice and an agent's shell with its tokens, give the game one environment."""
    probe = exports_module()
    session = {"HOME": "/home/x", "DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0",
               "XDG_RUNTIME_DIR": "/run/user/1000", "LANG": "en_US.UTF-8"}
    terminal = {**session, "PATH": "/v/bin:/v/bin:/usr/bin", "OLDPWD": "/tmp", "SHLVL": "2"}
    agent = {**session, "PATH": "/v/bin:/usr/bin", "CLAUDE_CODE_SESSION_ID": "s",
             "SOME_TOKEN": "t"}
    assert probe.game_environment(terminal) == probe.game_environment(agent) == {
        **session, "PATH": probe.GAME_PATH}


def test_the_game_s_xauthority_is_one_link_in_every_login(tmp_path: Path, monkeypatch) -> None:
    """The X authority file's name ends in a suffix that is new at every login
    (the probe review of 2026-09-24, W2), so the game is given one link for it:
    two logins give one game environment, and before each launch the link is
    pointed at that login's file. Only a link is replaced, the file's name is
    not recorded, and a caller with no XAUTHORITY gives the game none."""
    probe = exports_module()
    monkeypatch.setattr(probe, "OUT", tmp_path / "out")
    link = tmp_path / "out" / "xauthority"
    runtime = tmp_path / "run"
    runtime.mkdir()
    logins = []
    for suffix, cookie in (("AAAAAA", b"cookie one"), ("BBBBBB", b"cookie two")):
        (runtime / f".mutter-Xwaylandauth.{suffix}").write_bytes(cookie)
        logins.append({"HOME": "/home/x", "DISPLAY": ":0", "XDG_RUNTIME_DIR": str(runtime),
                       "XAUTHORITY": str(runtime / f".mutter-Xwaylandauth.{suffix}")})
    one, two = (probe.game_environment(env) for env in logins)
    assert one == two == {"HOME": "/home/x", "DISPLAY": ":0", "XDG_RUNTIME_DIR": str(runtime),
                          "XAUTHORITY": str(link), "PATH": probe.GAME_PATH}
    assert probe.environment_record(one)["sha256"] == probe.environment_record(two)["sha256"]
    done = probe.link_xauthority(logins[0])
    assert done == {"given_as": str(link), "a_link_to_the_caller_s_file": True,
                    "the_file_exists": True}
    assert "AAAAAA" not in json.dumps(done)
    assert link.is_symlink() and link.read_bytes() == b"cookie one"
    (runtime / ".mutter-Xwaylandauth.AAAAAA").unlink()   # the next login: the last one's file is gone
    assert link.is_symlink() and not link.exists()
    assert probe.link_xauthority(logins[1])["the_file_exists"] is True
    assert link.read_bytes() == b"cookie two"
    probe.link_xauthority({**logins[1], "XAUTHORITY": str(link)})   # the link itself: kept
    assert link.read_bytes() == b"cookie two"
    assert sorted(p.name for p in link.parent.iterdir()) == ["xauthority"]
    with pytest.raises(SystemExit, match="leads to no X authority file"):   # a stale shell
        probe.link_xauthority(logins[0])
    assert link.read_bytes() == b"cookie two"                         # left as it was
    link.unlink()                                    # the link itself, with no link yet
    with pytest.raises(SystemExit, match="leads back to"):
        probe.link_xauthority({**logins[1], "XAUTHORITY": str(link)})
    probe.link_xauthority(logins[1])
    bare = {k: v for k, v in logins[0].items() if k != "XAUTHORITY"}
    assert "XAUTHORITY" not in probe.game_environment(bare)
    assert probe.link_xauthority(bare) is None
    link.unlink()
    link.write_bytes(b"someone's file")
    with pytest.raises(SystemExit, match="is not the link this runner makes for XAUTHORITY"):
        probe.link_xauthority(logins[0])
    assert link.read_bytes() == b"someone's file" and not link.is_symlink()


def test_the_runner_rebuilds_a_mirror_that_is_not_what_it_built(tmp_path: Path,
                                                                 monkeypatch) -> None:
    """Missing, from before the save directory was its own, or written along
    the path to it: each is a reason, named; a mirror as built is none."""
    probe = exports_module()
    M = mirror_module()
    install = fake_install(tmp_path)
    mirror = tmp_path / "mirror"
    monkeypatch.setattr(probe, "INSTALL", install)
    monkeypatch.setattr(probe, "MIRROR", mirror)
    assert probe.mirror_rebuild_reasons(False) == ["there is no mirror"]
    M.build(install, mirror, M.DEFAULT_SYMLINK_THRESHOLD, force=False)
    assert probe.mirror_rebuild_reasons(False) == []
    assert probe.mirror_rebuild_reasons(True) == ["--rebuild-mirror"]
    (mirror / "resource" / "gd_PC" / "NEWFILE").write_text("x")
    assert probe.mirror_rebuild_reasons(False) == [
        "on the way to its save directory: created resource/gd_PC/NEWFILE"]
    M.build(install, mirror, M.DEFAULT_SYMLINK_THRESHOLD, force=True)
    assert probe.mirror_rebuild_reasons(False) == []
    old = tmp_path / "old"
    M.build(fake_install(tmp_path / "o", saves=False), old, M.DEFAULT_SYMLINK_THRESHOLD,
            force=False)
    monkeypatch.setattr(probe, "MIRROR", old)
    assert probe.mirror_rebuild_reasons(False) == [
        "its resource/gd_PC/SAVEDATA is shared with the installation"]


# ---------------------------------------------------------- the pinned saves

EMPTY = hashlib.sha256(b"").hexdigest()
CHAO = hashlib.sha256(b"chao").hexdigest()          # fake_install's save


def write_pin(path: Path, files: dict) -> Path:
    """A save-state pin as tools/oracle/save_state.py reads one."""
    sys.path.insert(0, str(HERE))
    import save_state as SS
    path.write_text(json.dumps({"schema": SS.SCHEMA, "directory": "resource/gd_PC/SAVEDATA",
                                "files": files, "digest": SS.digest(files),
                                "state": "a made-up start", "why": "a test's",
                                "restore": SS.RESTORE}))
    return path


def runner_tree(tmp_path: Path, monkeypatch, pinned: dict):
    """The runner pointed at fake_install, a mirror and its output under
    tmp_path, the executable's pin not consulted, and the saves pinned to
    `pinned`."""
    probe = exports_module()
    install = fake_install(tmp_path)
    monkeypatch.setattr(probe, "INSTALL", install)
    monkeypatch.setattr(probe, "MIRROR", tmp_path / "mirror")
    monkeypatch.setattr(probe, "OUT", tmp_path / "out")
    monkeypatch.setattr(probe, "verify_executable", lambda: "0" * 64)
    monkeypatch.setattr(probe.SS, "PIN", write_pin(tmp_path / "save-state.json", pinned))
    session_xauthority = tmp_path / "session-xauthority"
    session_xauthority.write_bytes(b"cookie")
    monkeypatch.setenv("XAUTHORITY", str(session_xauthority))     # never the host's own
    return probe, install


def test_the_runner_refuses_to_start_unless_the_installation_s_saves_are_the_pin(
        tmp_path: Path, monkeypatch) -> None:
    """The user's ruling of 2026-09-24: the saves are a recorded input. Other
    bytes, a save left over and a missing save are each refused before a
    mirror is built or a probe compiled, and the message says how to restore."""
    probe, install = runner_tree(tmp_path, monkeypatch, {"SONIC2B__ALF": EMPTY})
    saves = install / "resource" / "gd_PC" / "SAVEDATA"
    with pytest.raises(SystemExit) as e:
        probe.run(argparse.Namespace(), tmp_path / "captures" / "c", (640, 480), [])
    assert e.value.code == (
        f"the saves in {install}/resource/gd_PC/SAVEDATA are not the pinned start state "
        f"({tmp_path / 'save-state.json'}, a made-up start): changed SONIC2B__ALF "
        f"({CHAO[:12]} where the pin has {EMPTY[:12]}). Restore them with "
        "`.venv/bin/python tools/oracle/save_state.py restore` from the repository root")
    (saves / "SONIC2B__ALF").write_bytes(b"")
    (saves / "SONIC2B__S01").write_bytes(b"")
    with pytest.raises(SystemExit, match=r": extra SONIC2B__S01 \(e3b0c44298fc\)\. Restore"):
        probe.run(argparse.Namespace(), tmp_path / "captures" / "c", (640, 480), [])
    (saves / "SONIC2B__S01").unlink()
    (saves / "SONIC2B__ALF").unlink()
    with pytest.raises(SystemExit, match=r": missing SONIC2B__ALF\. Restore"):
        probe.run(argparse.Namespace(), tmp_path / "captures" / "c", (640, 480), [])
    assert not (tmp_path / "mirror").exists() and not (tmp_path / "out").exists()
    (saves / "SONIC2B__ALF").write_bytes(b"")
    assert probe.pinned_saves().files == {"SONIC2B__ALF": EMPTY}


def fake_game(tmp_path: Path, monkeypatch, probe, *, writes_save: bool,
              launched: dict | None = None) -> argparse.Namespace:
    """A probe build and a game that write one capture, and with `writes_save`
    the save the upgrade's pickup writes; the arguments run() takes. With
    `launched`, the game keeps there the environment it was launched with and
    the file its XAUTHORITY led to at that moment."""
    source = tmp_path / "p.c"
    source.write_text("/* a probe */\n")

    def compile_probe(source, mod_id, defines):
        dll = tmp_path / f"{mod_id}.dll"
        dll.write_bytes(b"MZ")
        return dll, "d" * 64

    def run(cmd, **kw):
        if cmd[0] == "xrandr":
            return subprocess.CompletedProcess(cmd, 0, XRANDR, "")
        mirror = Path(kw["cwd"])
        if launched is not None:
            launched["env"] = kw.get("env")
            xauthority = (kw.get("env") or {}).get("XAUTHORITY")
            launched["xauthority_leads_to"] = xauthority and os.path.realpath(xauthority)
        (mirror / "mods" / "cap.csv").write_text("tick,v\n1,1\n2,2\n")
        if writes_save:
            (mirror / "resource" / "gd_PC" / "SAVEDATA" / "SONIC2B__S01").write_bytes(b"")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(probe, "compile_probe", compile_probe)
    monkeypatch.setattr(probe.subprocess, "run", run)
    monkeypatch.setattr(probe, "host_game_controllers", lambda: [])    # no host inventory read
    monkeypatch.setattr(probe, "host_hid_devices", lambda: [])
    return argparse.Namespace(
        name="c", rebuild_mirror=False, builds=[("m", source)], defines={}, profile_mod=[],
        capture=["cap.csv"], binary_capture=[], policies={}, min_rows=1, require_varying=[],
        trim_torn_row=False, device_profile=False, skip_intro=True, pause_on_inactive=False,
        text_language=1, voice_language=1, timeout=60)


def test_each_receipt_records_the_pin_and_the_saves_the_run_started_and_ended_with(
        tmp_path: Path, monkeypatch, capsys) -> None:
    """The game's save stays in the mirror, the installation is untouched, and
    the receipt carries the pin's digest and whether the start matched it."""
    probe, install = runner_tree(tmp_path, monkeypatch, {"SONIC2B__ALF": CHAO})
    args = fake_game(tmp_path, monkeypatch, probe, writes_save=True)
    probe.run(args, tmp_path / "captures" / "c", (640, 480), [])
    assert capsys.readouterr().out.startswith("SA2_ORACLE_CAPTURE_OK name=c exit=0 ")
    rec = json.loads((tmp_path / "captures" / "c" / "receipt.json").read_text())
    saves = rec["session"]["saves"]
    assert (saves["at_start"], saves["at_end"]) == (
        {"SONIC2B__ALF": CHAO}, {"SONIC2B__ALF": CHAO, "SONIC2B__S01": EMPTY})
    assert saves["pin"] == {"file": str(tmp_path / "save-state.json"),
                            "digest": probe.SS.digest({"SONIC2B__ALF": CHAO}),
                            "state": "a made-up start"}
    assert saves["matches_pin"] is True and rec["installation_untouched"] is True
    assert probe.SS.problems(install, probe.SS.load()) == []


def test_the_receipt_records_the_environment_the_game_was_launched_with(
        tmp_path: Path, monkeypatch, capsys) -> None:
    """tools/oracle/record_stage.py holds every replay to its recording's
    session.environment, so the record must be the environment the game got,
    the fixed one (game_environment), never the caller's; and the game's
    XAUTHORITY led to the caller's file when it was launched (the probe
    review of 2026-09-24, W1 and W2)."""
    probe, install = runner_tree(tmp_path, monkeypatch, {"SONIC2B__ALF": CHAO})
    monkeypatch.setenv("SOME_TOKEN", "t")
    launched: dict = {}
    args = fake_game(tmp_path, monkeypatch, probe, writes_save=False, launched=launched)
    probe.run(args, tmp_path / "captures" / "c", (640, 480), [])
    rec = json.loads((tmp_path / "captures" / "c" / "receipt.json").read_text())
    assert launched["env"] == probe.game_environment(dict(os.environ))
    assert "SOME_TOKEN" not in launched["env"] and launched["env"]["PATH"] == probe.GAME_PATH
    assert launched["env"]["XAUTHORITY"] == str(tmp_path / "out" / "xauthority")
    assert launched["xauthority_leads_to"] == str(tmp_path / "session-xauthority")
    assert rec["session"]["game_environment"] == launched["env"]
    assert rec["session"]["environment"] == probe.environment_record(launched["env"])
    assert rec["session"]["xauthority"] == {"given_as": str(tmp_path / "out" / "xauthority"),
                                            "a_link_to_the_caller_s_file": True,
                                            "the_file_exists": True}


def test_a_run_whose_saves_at_start_were_not_the_pin_is_refused(tmp_path: Path,
                                                                  monkeypatch, capsys) -> None:
    """The negative control: a reset that left the previous run's save in the
    mirror starts the game from other saves, and the verdict says so."""
    probe, install = runner_tree(tmp_path, monkeypatch, {"SONIC2B__ALF": CHAO})
    args = fake_game(tmp_path, monkeypatch, probe, writes_save=False)

    def reset_that_leaves_a_save(install_dir, mirror):
        (mirror / "resource" / "gd_PC" / "SAVEDATA" / "SONIC2B__S01").write_bytes(b"")
        return []

    monkeypatch.setattr(probe.mirror_install, "reset_save_dir", reset_that_leaves_a_save)
    with pytest.raises(SystemExit) as e:
        probe.run(args, tmp_path / "captures" / "c", (640, 480), [])
    assert e.value.code == 1
    out = capsys.readouterr().out
    assert out.startswith("SA2_ORACLE_CAPTURE_PROBLEM name=c exit=0 ")
    assert "  SAVES NOT PINNED: the run started from" in out
    rec = json.loads((tmp_path / "captures" / "c" / "receipt.json").read_text())
    assert rec["session"]["saves"]["matches_pin"] is False


# --------------------------------------------------------------- comparer

COMPARE = HERE / "compare_captures.py"


def test_compare_identical_streams(tmp_path: Path) -> None:
    body = "tick,a,b\n1,1,1\n2,2,2\n3,3,3\n"
    (tmp_path / "l.csv").write_text(body)
    (tmp_path / "r.csv").write_text(body)
    result = run(str(COMPARE), "--identity", "tick",
                 str(tmp_path / "l.csv"), str(tmp_path / "r.csv"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SA2_ORACLE_IDENTICAL identities=3" in result.stdout


def test_compare_reports_the_first_divergence_not_the_last(tmp_path: Path) -> None:
    (tmp_path / "l.csv").write_text("tick,a,b\n1,1,1\n2,2,2\n3,3,3\n")
    (tmp_path / "r.csv").write_text("tick,a,b\n1,1,1\n2,9,2\n3,7,3\n")
    result = run(str(COMPARE), "--identity", "tick",
                 str(tmp_path / "l.csv"), str(tmp_path / "r.csv"))
    assert result.returncode == 1
    assert "first tick=2 field=a" in result.stdout
    assert "'2'" in result.stdout and "'9'" in result.stdout


def test_compare_refuses_two_header_only_streams(tmp_path: Path) -> None:
    """The exact shape of a comparison that cannot fail: two empty operands
    compare equal."""
    (tmp_path / "l.csv").write_text("tick,a,b\n")
    (tmp_path / "r.csv").write_text("tick,a,b\n")
    result = run(str(COMPARE), "--identity", "tick",
                 str(tmp_path / "l.csv"), str(tmp_path / "r.csv"))
    assert result.returncode == 2
    assert "header-only" in result.stderr


def test_compare_refuses_a_thin_overlap(tmp_path: Path) -> None:
    (tmp_path / "l.csv").write_text("tick,a\n1,1\n2,2\n3,3\n")
    (tmp_path / "r.csv").write_text("tick,a\n1,1\n9,9\n")
    result = run(str(COMPARE), "--identity", "tick",
                 "--require-overlap", "3",
                 str(tmp_path / "l.csv"), str(tmp_path / "r.csv"))
    assert result.returncode == 2
    assert "not a comparison" in result.stderr


def test_compare_refuses_different_headers(tmp_path: Path) -> None:
    (tmp_path / "l.csv").write_text("tick,a\n1,1\n")
    (tmp_path / "r.csv").write_text("tick,a,b\n1,1,1\n")
    result = run(str(COMPARE), "--identity", "tick",
                 str(tmp_path / "l.csv"), str(tmp_path / "r.csv"))
    assert result.returncode == 2
    assert "headers differ" in result.stderr


def test_compare_catches_a_width_change_at_the_same_tick(tmp_path: Path) -> None:
    (tmp_path / "l.csv").write_text("tick,a,b\n1,1,1\n2,0\n")
    (tmp_path / "r.csv").write_text("tick,a,b\n1,1,1\n2,0,0\n")
    result = run(str(COMPARE), "--identity", "tick",
                 str(tmp_path / "l.csv"), str(tmp_path / "r.csv"))
    assert result.returncode == 1
    assert "<row width>" in result.stdout


# ------------------------------------------------------------ record_stage


def test_a_recording_becomes_a_replay_header_row_for_row(tmp_path: Path) -> None:
    """Every OnInput row of a recording is one row of the replay script, keyed
    by tick and ordinal, with each field's value as the game read it."""
    sys.path.insert(0, str(HERE))
    import record_stage as R
    rec = tmp_path / "sa2-input.csv"
    rec.write_text("tick,ord,on,off,press,release,r,l,x1,y1,x2,y2,old\n"
                   "0,0,0x0,0xffffffff,0x0,0x0,0x0,0x0,0x0,0x0,0x0,0x0,0x0\n"
                   "5,1,0x400,0xfffffbff,0x400,0x0,0x0,0x0,0x0,0xff81,0x0,0x0,0x0\n")
    info = R.script(rec, tmp_path / "s.h")
    text = (tmp_path / "s.h").read_text()
    assert info["rows"] == 2
    assert "in_rows[][13]" in text and "in_count = 2u" in text
    assert "{5,1,1024,4294966271,1024,0,0,0,0,65409,0,0,0}" in text


def test_an_empty_recording_is_refused(tmp_path: Path) -> None:
    sys.path.insert(0, str(HERE))
    import record_stage as R
    rec = tmp_path / "sa2-input.csv"
    rec.write_text("tick,ord,on,off,press,release,r,l,x1,y1,x2,y2,old\n")
    with pytest.raises(SystemExit):
        R.script(rec, tmp_path / "s.h")


# ---------------------------------------------------------------- the word log
#
# harness/replay/native_tick_probe.c keeps a word log of the game's data
# section and writes its row with the named fields alone; the runner checks
# the log against that row (tools/s4/wordlog.py check_capture), builds the tick
# capture every consumer reads from it, and keeps it compressed (the runner's
# docstring, item 5). The logs here are written by synthetic_word_log in the
# format harness/replay/word_log_core.h defines, over a made-up section: every
# byte is zero but the words a test sets, none copied from the executable.

import struct  # noqa: E402

WL_BASE, WL_SIZE = 0x00917000, 0x01D59598
FRAME_COUNT = 0x0174B038             # tick_fields.h: frame_count, 4 bytes
GAME_MODE = 0x0174B040               # tick_fields.h: game_mode, 4 bytes
WL_CORE = HERE.parent.parent / "harness" / "replay" / "word_log_core.h"
FNV64_BASIS, FNV64_PRIME, U64 = 14695981039346656037, 1099511628211, 1 << 64


def core_writes_end_check() -> bool:
    """Whether the tree's word_log_core.h defines the end-of-run check, an
    "SA2V" record after the last tick's, which the reader then requires of a
    native log (U1's fold); the logs here follow the tree's format."""
    return b'"SA2V"' in WL_CORE.read_bytes()


def fnv1a64_mostly_zero(words: dict[int, int], count: int) -> int:
    """FNV-1a 64 over `count` 32-bit words that are zero but `words` (index ->
    value), a run of zeros taken at once: each zero word multiplies by the
    prime."""
    h, at = FNV64_BASIS, 0
    for i in sorted(words):
        h = h * pow(FNV64_PRIME, i - at, U64) % U64
        h = (h ^ words[i]) * FNV64_PRIME % U64
        at = i + 1
    return h * pow(FNV64_PRIME, count - at, U64) % U64


def synthetic_word_log(path: Path, ticks: int, *, rule: int = 1, reason: int = 1,
                       buffer_bytes: int = 192 << 20, changes: dict | None = None) -> Path:
    """A version-2 word log of `ticks` ticks (word_log_core.h's format): the
    header, the writer's two blocks, a region map at Init, a keyframe with
    game_mode 5, one record per tick in which frame_count is the tick and each
    of `changes` (tick -> {va: value}) is written, the end-of-run check where
    the tree's format has one (core_writes_end_check), the region map at the
    end and the end record. `rule` and `reason` are the header's end_rule and
    the end record's reason (1 TICK_LIMIT, 2 INPUT_END)."""
    key = bytearray(WL_SIZE)
    struct.pack_into("<I", key, GAME_MODE - WL_BASE, 5)
    last = {(GAME_MODE - WL_BASE) // 4: 5}   # the section's words at the last tick
    regions = [(0x00400000, 0x02700000), (0xB0000000, WL_SIZE), (0xC0000000, buffer_bytes)]
    rate = 10_000_000
    out = bytearray(b"SA2F" + struct.pack("<8I3Q", 2, WL_BASE, WL_SIZE, buffer_bytes, rule,
                                           ticks if rule == 1 else 0, 16, 0, rate, 0, 0))
    out += b"SA2O" + struct.pack("<I", 2) + struct.pack("<4I", 0xB0000000, WL_SIZE,
                                                        0xC0000000, buffer_bytes)
    out += b"SA2R" + struct.pack("<3I", 0, len(regions), 0)
    out += b"".join(struct.pack("<2I", *r) for r in regions)
    out += b"SA2K" + struct.pack("<3I", 0, WL_BASE, WL_SIZE) + key
    for t in range(1, ticks + 1):
        words = {(FRAME_COUNT - WL_BASE) // 4: t}
        words.update({(va - WL_BASE) // 4: v for va, v in ((changes or {}).get(t) or {}).items()})
        last.update(words)
        runs: list[list[int]] = []
        for w in sorted(words):
            if runs and runs[-1][0] + len(runs[-1]) - 1 == w:
                runs[-1].append(words[w])
            else:
                runs.append([w, words[w]])
        out += b"SA2D" + struct.pack("<I", t)
        for r in runs:
            out += struct.pack("<2I", r[0], len(r) - 1) + struct.pack(f"<{len(r) - 1}I", *r[1:])
        enter = t * 166_670
        out += struct.pack("<3I", 0xFFFFFFFF, len(runs), sum(len(r) - 1 for r in runs))
        out += struct.pack("<3Q", enter, enter + 100, enter + 5_000)
    if core_writes_end_check():
        # last_tick, unscanned, 0, and the hash of the section rebuilt there.
        out += b"SA2V" + struct.pack("<3IQ", ticks, 0, 0,
                                     fnv1a64_mostly_zero(last, WL_SIZE // 4))
    out += b"SA2R" + struct.pack("<3I", ticks, len(regions), 0)
    out += b"".join(struct.pack("<2I", *r) for r in regions)
    end = (ticks + 1) * 166_670
    out += b"SA2E" + struct.pack("<4I", ticks, 0, reason, 0) + struct.pack("<3Q", end, end,
                                                                           len(out))
    path.write_bytes(bytes(out))
    return path


def test_the_end_check_s_hash_takes_a_run_of_zeros_as_each_zero_word():
    words = [0, 0, 7, 0, 0, 0xFFFFFFFF, 0, 0, 0]
    h = FNV64_BASIS
    for w in words:
        h = (h ^ w) * FNV64_PRIME % U64
    assert fnv1a64_mostly_zero({2: 7, 5: 0xFFFFFFFF}, len(words)) == h
    assert fnv1a64_mostly_zero({}, 3) == FNV64_BASIS * FNV64_PRIME ** 3 % U64


def written_row(ticks: int, changes: dict | None = None) -> str:
    """The tick probe's row for synthetic_word_log(ticks, changes=changes), as
    tick_probe_core.h writes it (`tick,` then each named field `0x%08x`), made
    from the words the log was written with and not by the reader: the good
    run then holds the reader's named columns to the probe's format (the
    runner review of 2026-09-25, N6)."""
    probe = exports_module()
    fields = probe.WL.named_fields()
    out = ["tick," + ",".join(n for n, _, _ in fields)]
    for t in range(1, ticks + 1):
        memory = {FRAME_COUNT: t, GAME_MODE: 5, **((changes or {}).get(t) or {})}
        out.append(f"{t}," + ",".join(f"0x{memory.get(va, 0) & ((1 << 8 * w) - 1):08x}"
                                      for _, va, w in fields))
    return "\n".join(out) + "\n"


def probe_row(log: Path) -> str:
    """The tick probe's own row for `log`: the named fields of the capture the
    log gives, as tick_probe_core.h writes them."""
    probe = exports_module()
    names = 1 + len(probe.WL.named_fields())
    return "".join(",".join(line.split(",")[:names]) + "\n"
                   for line in probe.WL.capture_csv(log).splitlines())


def word_log_game(tmp_path: Path, monkeypatch, probe, *, ticks: int = 6, log: bool = True,
                  row_change: tuple[int, str] | None = None) -> argparse.Namespace:
    """A tick probe build and a game that writes its row and, with `log`, its
    word log; with `row_change` (tick, value), the row's frame_count at that
    tick is another than the log's. Every command but the game and xrandr runs
    for real: the reader and zstd."""
    source = tmp_path / "harness" / "replay" / probe.TICK_PROBE
    source.parent.mkdir(parents=True)
    source.write_text("/* a tick probe */\n")
    made = synthetic_word_log(tmp_path / "made.bin", ticks)
    row = probe_row(made)
    if row_change:
        lines = row.splitlines(keepends=True)
        cells = lines[row_change[0]].split(",")
        cells[1] = row_change[1]
        lines[row_change[0]] = ",".join(cells)
        row = "".join(lines)
    real_run = subprocess.run

    def compile_probe(source, mod_id, defines):
        dll = tmp_path / f"{mod_id}.dll"
        dll.write_bytes(b"MZ")
        return dll, "d" * 64

    def run(cmd, **kw):
        if cmd[0] == "xrandr":
            return subprocess.CompletedProcess(cmd, 0, XRANDR, "")
        if cmd[0] != "timeout":
            return real_run(cmd, **kw)
        mods = Path(kw["cwd"]) / "mods"
        (mods / probe.TICK_CAPTURE).write_text(row)
        if log:
            (mods / probe.WORD_LOG).write_bytes(made.read_bytes())
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(probe, "compile_probe", compile_probe)
    monkeypatch.setattr(probe.subprocess, "run", run)
    # The idle measure reads the captures directory: this test's, not the oracle's.
    monkeypatch.setattr(probe, "CAPTURES", tmp_path / "captures")
    monkeypatch.setattr(probe, "host_game_controllers", lambda: [])
    monkeypatch.setattr(probe, "host_hid_devices", lambda: [])
    return argparse.Namespace(
        name="c", rebuild_mirror=False, builds=[("tick_probe", source)], defines={},
        profile_mod=[], capture=[probe.TICK_CAPTURE], binary_capture=[probe.WORD_LOG],
        policies={}, min_rows=ticks, require_varying=["frame_count"], trim_torn_row=False,
        device_profile=False, skip_intro=True, pause_on_inactive=False, text_language=1,
        voice_language=1, timeout=60)


def test_the_tick_capture_is_built_from_the_word_log_and_the_log_kept_compressed(
        tmp_path: Path, monkeypatch, capsys) -> None:
    """The good run: the probe's row is kept as sa2-tick-named.csv, the capture
    every consumer reads is the reader's, with the tree's digest columns, the
    log is kept as .zst that decompresses to it, and the receipt records the
    log with the idle time and the load average."""
    probe, install = runner_tree(tmp_path, monkeypatch, {"SONIC2B__ALF": CHAO})
    args = word_log_game(tmp_path, monkeypatch, probe)
    probe.run(args, tmp_path / "captures" / "c", (640, 480), [])
    out = capsys.readouterr().out
    assert out.startswith("SA2_ORACLE_CAPTURE_OK name=c exit=0 ")
    d = tmp_path / "captures" / "c"
    made = tmp_path / "made.bin"
    assert sorted(p.name for p in d.iterdir()) == [
        "native-run.log", "receipt.json", "sa2-tick-capture.csv", "sa2-tick-named.csv",
        "sa2-word-log.bin.zst"]
    assert (d / "sa2-tick-capture.csv").read_text() == probe.WL.capture_csv(made)
    header = (d / "sa2-tick-capture.csv").read_text().splitlines()[0].split(",")
    assert header[23:] == [f"d_{b:08x}" for b in probe.WL.mask_blocks()] and len(header) > 23
    assert (d / "sa2-tick-named.csv").read_text() == probe_row(made) == written_row(6)
    named = 1 + len(probe.WL.named_fields())
    assert "".join(",".join(line.split(",")[:named]) + "\n" for line in (
        d / "sa2-tick-capture.csv").read_text().splitlines()) == written_row(6)
    unzipped = subprocess.run(["zstd", "-dc", str(d / "sa2-word-log.bin.zst")],
                              capture_output=True, check=True).stdout
    assert unzipped == made.read_bytes()
    rec = json.loads((d / "receipt.json").read_text())
    arts = {a["file"]: a for a in rec["artifacts"]}
    assert arts["sa2-tick-named.csv"]["collected_as"] == "sa2-tick-capture.csv"
    assert arts["sa2-tick-capture.csv"]["check"]["sha256"] == probe.sha256(
        d / "sa2-tick-capture.csv")
    assert arts["sa2-tick-capture.csv"]["built_from_log"]["digest_inputs"][
        "harness/replay/tick_digest.h"] == probe.sha256(probe.WL.MASK)
    log = arts["sa2-word-log.bin.zst"]
    assert (log["collected_as"], log["check"]["ok"], log["check"]["log_sha256"],
            log["check"]["sha256"]) == ("sa2-word-log.bin", True, probe.sha256(made),
                                        probe.sha256(d / "sa2-word-log.bin.zst"))
    w = rec["word_log"]
    assert (w["ok"], w["ticks"], w["end_reason"], w["whole"], w["sha256"], w["stored_as"]) == (
        True, 6, "tick_limit", True, probe.sha256(made), "sa2-word-log.bin.zst")
    assert w["capture_check"]["named_cells_compared"] == 6 * len(probe.WL.named_fields())
    assert w["capture_ok"] is True
    assert w["cost"]["scan_ms"]["mean"] == pytest.approx(0.49)
    session = rec["session"]
    assert set(session["idle_before_run"]) >= {"seconds", "since_last_game_exit_s",
                                               "since_newest_capture_s"}
    assert len(session["load_average"]["at_launch"]) == 3
    assert session["idle_before_run_seconds"] == session["idle_before_run"]["seconds"]
    assert json.loads((tmp_path / "out" / "locks" / probe.LAST_EXIT).read_text())["name"] == "c"
    assert "  word log: checked, 6 ticks, ended by tick_limit, kept as sa2-word-log.bin.zst" in out


def test_a_run_whose_tick_probe_left_no_word_log_is_refused(tmp_path: Path, monkeypatch,
                                                            capsys) -> None:
    probe, install = runner_tree(tmp_path, monkeypatch, {"SONIC2B__ALF": CHAO})
    args = word_log_game(tmp_path, monkeypatch, probe, log=False)
    with pytest.raises(SystemExit) as e:
        probe.run(args, tmp_path / "captures" / "c", (640, 480), [])
    assert e.value.code == 1
    out = capsys.readouterr().out
    assert out.startswith("SA2_ORACLE_CAPTURE_PROBLEM name=c exit=0 ")
    assert "  MISSING sa2-tick-capture.csv: the tick probe left no sa2-word-log.bin" in out
    d = tmp_path / "captures" / "c"
    assert not (d / "sa2-tick-capture.csv").exists() and (d / "sa2-tick-named.csv").is_file()
    rec = json.loads((d / "receipt.json").read_text())
    assert rec["word_log"]["ok"] is False
    assert [a["check"]["ok"] for a in rec["artifacts"] if a["file"] == "sa2-word-log.bin"] == [
        False]


def test_a_word_log_that_is_not_the_probe_s_row_refuses_the_capture(tmp_path: Path,
                                                                    monkeypatch, capsys) -> None:
    """The negative control of the good run: one cell of the row differs from
    the log's bytes, so no capture is built, and the log is kept to look at."""
    probe, install = runner_tree(tmp_path, monkeypatch, {"SONIC2B__ALF": CHAO})
    args = word_log_game(tmp_path, monkeypatch, probe, row_change=(3, "0x00000063"))
    with pytest.raises(SystemExit):
        probe.run(args, tmp_path / "captures" / "c", (640, 480), [])
    out = capsys.readouterr().out
    assert out.startswith("SA2_ORACLE_CAPTURE_PROBLEM name=c exit=0 ")
    assert "      frame_count differs from the log's bytes on 1 ticks, first 3" in out
    d = tmp_path / "captures" / "c"
    assert not (d / "sa2-tick-capture.csv").exists() and (d / "sa2-word-log.bin.zst").is_file()


def test_a_capture_check_that_raises_is_a_refusal_with_a_receipt(tmp_path: Path, monkeypatch,
                                                                 capsys) -> None:
    """The reader's check returns its refusals, but one that raises after the
    game ran must still leave a receipt that names it, and no capture."""
    probe, install = runner_tree(tmp_path, monkeypatch, {"SONIC2B__ALF": CHAO})
    args = word_log_game(tmp_path, monkeypatch, probe)

    def raises(*a, **k):
        raise probe.WL.WordLogError("the reader exited 3")

    monkeypatch.setattr(probe.WL, "check_capture", raises)
    with pytest.raises(SystemExit) as e:
        probe.run(args, tmp_path / "captures" / "c", (640, 480), [])
    assert e.value.code == 1
    assert capsys.readouterr().out.startswith("SA2_ORACLE_CAPTURE_PROBLEM name=c exit=0 ")
    d = tmp_path / "captures" / "c"
    rec = json.loads((d / "receipt.json").read_text())
    assert rec["word_log"]["problems"] == ["the capture check could not run: the reader exited 3"]
    assert not (d / "sa2-tick-capture.csv").exists() and (d / "sa2-word-log.bin.zst").is_file()


@pytest.mark.parametrize("argv, why", [
    (["--build", "tick_probe=harness/replay/native_tick_probe.c", "--capture",
      "sa2-tick-capture.csv"], "pass --binary-capture sa2-word-log.bin, a runner flag, so "
                              "before '--'"),
    (["--build", "tick_probe=harness/replay/native_tick_probe.c", "--capture", "a.csv",
      "--binary-capture", "sa2-word-log.bin"], "pass --capture sa2-tick-capture.csv"),
    (["--build", "stage_trace=harness/replay/native_stage_trace.c", "--binary-capture",
      "sa2-word-log.bin"], "no --build builds it"),
    (["--build", "tick_probe=harness/replay/native_tick_probe.c", "--binary-capture",
      "sa2-word-log.bin", "--", "--level", "metalharbor", "--binary-capture", "x"],
     "['--binary-capture'] follow '--', where every argument goes to the game"),
    (["--build", "tick_probe=harness/replay/native_tick_probe.c", "--binary-capture",
      "sa2-word-log.bin", "--", "--capture=x"], "['--capture=x'] follow '--'"),
    (["--build", "stage_trace=harness/replay/native_stage_trace.c", "--profile-mod",
      "tick_probe"], "--profile-mod tick_probe loads the mirror's last native_tick_probe.c build"),
])
def test_a_run_that_would_lose_its_word_log_is_refused_before_anything_runs(
        tmp_path: Path, monkeypatch, argv, why) -> None:
    """Refused before the lease, the mirror, a build or the game: a tick probe
    without its log flag or its row, a log no probe writes, and a runner flag
    after '--', which reaches the game (the judge's run B)."""
    probe = exports_module()
    monkeypatch.setattr(probe, "CAPTURES", tmp_path / "captures")

    def nothing(*a, **k):
        raise AssertionError("the run started")

    monkeypatch.setattr(probe, "lease", nothing)
    with pytest.raises(SystemExit) as e:
        probe.main(["--name", "c"] + argv)
    assert why in str(e.value.code)


def test_idle_is_the_most_recent_of_the_last_game_exit_and_the_newest_capture(
        tmp_path: Path, monkeypatch) -> None:
    probe = exports_module()
    monkeypatch.setattr(probe, "OUT", tmp_path / "out")
    monkeypatch.setattr(probe, "CAPTURES", tmp_path / "captures")
    assert probe.idle_before_run(now=1000.0)["seconds"] is None
    for name, when in (("old", 100.0), ("new", 400.0)):
        (tmp_path / "captures" / name).mkdir(parents=True)
        os.utime(tmp_path / "captures" / name, (when, when))
    got = probe.idle_before_run(now=1000.0)
    assert (got["seconds"], got["newest_capture"], got["since_last_game_exit_s"]) == (
        600.0, "new", None)
    probe.mark_game_exit("x")
    marker = tmp_path / "out" / "locks" / probe.LAST_EXIT
    marker.write_text(json.dumps({"name": "x", "epoch": 900.0}))
    got = probe.idle_before_run(now=1000.0)
    assert (got["seconds"], got["since_last_game_exit_s"], got["last_game_exit"],
            got["since_newest_capture_s"]) == (100.0, 100.0, "x", 600.0)


def test_a_compressed_log_is_kept_only_if_it_decompresses_to_the_log(tmp_path: Path,
                                                                     monkeypatch) -> None:
    """The log is removed only once its .zst is shown to be it; a compressor
    that wrote other bytes leaves the log and no .zst."""
    probe = exports_module()
    log = tmp_path / "sa2-word-log.bin"
    log.write_bytes(b"the log")
    other = tmp_path / "other"
    other.write_bytes(b"not the log")
    real_run = subprocess.run

    def run(cmd, **kw):
        if cmd[:1] == ["zstd"] and "-o" in cmd:
            return real_run(["zstd", "-q", "-f", "-o", cmd[cmd.index("-o") + 1], str(other)], **kw)
        return real_run(cmd, **kw)

    monkeypatch.setattr(probe.subprocess, "run", run)
    assert probe.zstd_compress(log) == {
        "problem": "the compressed log does not decompress to the log's bytes"}
    assert not (tmp_path / "sa2-word-log.bin.zst").exists() and log.read_bytes() == b"the log"
    monkeypatch.setattr(probe.subprocess, "run", real_run)
    got = probe.zstd_compress(log)
    assert (got["file"], got["level"], got["bytes"]) == (
        "sa2-word-log.bin.zst", 3, (tmp_path / "sa2-word-log.bin.zst").stat().st_size)


# ------------------------------------------------ the runner reviews of 2026-09-25


def launch_hook(probe, monkeypatch, hook) -> None:
    """Call `hook(mods)` as the game is launched, before the fake game runs."""
    inner = probe.subprocess.run

    def run(cmd, **kw):
        if cmd[0] == "timeout":
            hook(Path(kw["cwd"]) / "mods")
        return inner(cmd, **kw)

    monkeypatch.setattr(probe.subprocess, "run", run)


def test_a_word_log_an_earlier_run_left_in_mods_is_gone_before_the_game_starts(
        tmp_path: Path, monkeypatch, capsys) -> None:
    """W1: every boot's row is byte-identical, so the capture check cannot tell
    an earlier boot's log from this one's; the log is removed before the game
    runs, and a run whose game writes none is refused as leaving none."""
    probe, install = runner_tree(tmp_path, monkeypatch, {"SONIC2B__ALF": CHAO})
    args = word_log_game(tmp_path, monkeypatch, probe, log=False)
    real = probe.stage_profile

    def stage_profile(doc):
        out = real(doc)
        synthetic_word_log(probe.MIRROR / "mods" / probe.WORD_LOG, 6)   # an earlier run's
        return out

    monkeypatch.setattr(probe, "stage_profile", stage_profile)
    seen = []
    launch_hook(probe, monkeypatch, lambda mods: seen.append((mods / probe.WORD_LOG).exists()))
    with pytest.raises(SystemExit):
        probe.run(args, tmp_path / "captures" / "c", (640, 480), [])
    assert seen == [False]
    assert "  MISSING sa2-tick-capture.csv: the tick probe left no sa2-word-log.bin" in (
        capsys.readouterr().out)


def test_a_word_log_written_before_the_launch_is_an_earlier_run_s_and_refused(
        tmp_path: Path, monkeypatch, capsys) -> None:
    """W1: a log in mods/ older than this run's launch is never collected, even
    when it is the row's bytes on every tick."""
    probe, install = runner_tree(tmp_path, monkeypatch, {"SONIC2B__ALF": CHAO})
    args = word_log_game(tmp_path, monkeypatch, probe)
    inner = probe.subprocess.run

    def run(cmd, **kw):
        done = inner(cmd, **kw)
        if cmd[0] == "timeout":
            log = Path(kw["cwd"]) / "mods" / probe.WORD_LOG
            os.utime(log, (log.stat().st_atime, log.stat().st_mtime - 3600))
        return done

    monkeypatch.setattr(probe.subprocess, "run", run)
    with pytest.raises(SystemExit) as e:
        probe.run(args, tmp_path / "captures" / "c", (640, 480), [])
    assert e.value.code == 1
    out = capsys.readouterr().out
    assert out.startswith("SA2_ORACLE_CAPTURE_PROBLEM name=c exit=0 ")
    d = tmp_path / "captures" / "c"
    rec = json.loads((d / "receipt.json").read_text())
    assert rec["word_log"]["ok"] is False
    assert "before this run's launch, so it is an earlier run's" in rec["word_log"]["problems"][0]
    assert not (d / "sa2-tick-capture.csv").exists() and not (d / "sa2-word-log.bin.zst").exists()


def test_a_built_capture_that_fails_its_policy_says_so_beside_the_log_s_verdict(
        tmp_path: Path, monkeypatch, capsys) -> None:
    """N4: `ok` is the log's verdict and `capture_ok` the built capture's."""
    probe, install = runner_tree(tmp_path, monkeypatch, {"SONIC2B__ALF": CHAO})
    args = word_log_game(tmp_path, monkeypatch, probe)
    args.min_rows = 100
    with pytest.raises(SystemExit):
        probe.run(args, tmp_path / "captures" / "c", (640, 480), [])
    rec = json.loads((tmp_path / "captures" / "c" / "receipt.json").read_text())
    assert (rec["word_log"]["ok"], rec["word_log"]["capture_ok"]) == (True, False)


def refused_before_the_lease(probe, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(probe, "CAPTURES", tmp_path / "captures")

    def nothing(*a, **k):
        raise AssertionError("the run started")

    monkeypatch.setattr(probe, "lease", nothing)


TICK_RUN = ["--name", "c", "--build", "tick_probe=harness/replay/native_tick_probe.c",
            "--capture", "sa2-tick-capture.csv"]


def test_a_reader_that_cannot_be_built_refuses_the_run_before_the_lease(
        tmp_path: Path, monkeypatch) -> None:
    """N1: the reader is needed after the game; one that cannot be built is
    found before the lease, not after a run that cost the oracle."""
    probe = exports_module()
    refused_before_the_lease(probe, monkeypatch, tmp_path)

    def no_reader(mask=None):
        raise probe.WL.WordLogError("the pinned zig is missing: /nowhere/zig")

    monkeypatch.setattr(probe.WL, "reader", no_reader)
    with pytest.raises(SystemExit) as e:
        probe.main(TICK_RUN + ["--binary-capture", "sa2-word-log.bin"])
    assert str(e.value.code) == ("the word log's reader cannot be built or read, so this run's "
                                 "capture would be refused after the game: the pinned zig is "
                                 "missing: /nowhere/zig")


def test_a_runner_flag_is_never_taken_abbreviated(tmp_path: Path, monkeypatch, capsys) -> None:
    """N2: `--binary-cap` is no runner flag before '--' either, so a runner flag
    is spelled one way, the way runner_flags_among knows after '--'."""
    probe = exports_module()
    refused_before_the_lease(probe, monkeypatch, tmp_path)
    with pytest.raises(SystemExit) as e:
        probe.main(TICK_RUN + ["--binary-cap", "sa2-word-log.bin"])
    assert e.value.code == 2
    assert "error: unrecognized arguments: --binary-cap\n" in capsys.readouterr().err
