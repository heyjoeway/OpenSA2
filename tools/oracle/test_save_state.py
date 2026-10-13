#!/usr/bin/env python3
"""The pinned start state, made to fail one way at a time.

Subtitle: tools/oracle/save_state.py on made-up installations and overlays.

The pin is the saves every native and lifted run starts from (the user's
ruling of 2026-09-24). Each check here pairs the case it accepts with one it
must refuse: an extra save, other bytes, a missing save, a pin that lies about
its own digest, an overlay that keeps a save or an empty save directory. Every
tree is made up under pytest's tmp_path; the one real file read is the tracked
pin, Documentation/save-state.json, and the one real module besides the tool is
tools/s4/loop_run.py, for the defaults the lifted side shares with it.

Run:  .venv/bin/python -m pytest tools/oracle/test_save_state.py -q
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import save_state as SS  # noqa: E402

EMPTY = hashlib.sha256(b"").hexdigest()
CHAO = hashlib.sha256(b"chao").hexdigest()


def pin_doc(files: dict, **over) -> dict:
    doc = {"schema": SS.SCHEMA, "directory": "resource/gd_PC/SAVEDATA", "files": files,
           "digest": SS.digest(files), "state": "a made-up start", "why": "a test's",
           "restore": SS.RESTORE}
    doc.update(over)
    return doc


def pin_at(tmp_path: Path, files: dict, **over) -> SS.Pin:
    path = tmp_path / "save-state.json"
    path.write_text(json.dumps(pin_doc(files, **over)))
    return SS.load(path)


def install_with(tmp_path: Path, saves: dict[str, bytes] | None, name: str = "install") -> Path:
    """An installation's path to its save directory, holding `saves`, and one
    shipped file beside it; None makes no save directory."""
    install = tmp_path / name
    gd = install / "resource" / "gd_PC"
    gd.mkdir(parents=True)
    (gd / "small.prs").write_bytes(b"prs")
    if saves is not None:
        (gd / "SAVEDATA").mkdir()
        for n, data in saves.items():
            (gd / "SAVEDATA" / n).write_bytes(data)
    return install


def outside_saves(install: Path) -> dict:
    """Every file and link of `install` but its save directory, with its bytes."""
    out = {}
    for dirpath, dirs, files in os.walk(install):
        rel = Path(dirpath).relative_to(install)
        if rel.parts[:3] == ("resource", "gd_PC", "SAVEDATA"):
            continue
        for f in files:
            p = Path(dirpath) / f
            out[str(rel / f)] = os.readlink(p) if p.is_symlink() else p.read_bytes()
    return out


# ------------------------------------------------------------------ the pin

def test_the_tracked_pin_is_before_the_light_dash_pickup():
    """SONIC2B__ALF alone, an empty file: what every capture so far started
    from, and the digest of it (Documentation/save-state.json's why)."""
    pin = SS.load()
    assert pin.path == SS.PIN and pin.state == "before the Light Dash pickup"
    assert pin.files == {"SONIC2B__ALF": EMPTY}
    assert pin.digest == SS.digest({"SONIC2B__ALF": EMPTY}) == (
        "e4ac454bb534667fd0744f7b421308bb97359c55c0eab53fde8359d24eb3fd60")
    doc = json.loads(SS.PIN.read_text())
    assert doc["check"] == ".venv/bin/python tools/oracle/save_state.py check"


def test_the_digest_is_of_names_and_bytes_only():
    assert SS.digest({"B": EMPTY, "A": CHAO}) == SS.digest({"A": CHAO, "B": EMPTY})
    assert SS.digest({"A": CHAO}) != SS.digest({"A": EMPTY})
    assert SS.digest({"A": CHAO}) != SS.digest({"a": CHAO})
    assert SS.digest({}) == hashlib.sha256(b"{}").hexdigest()


@pytest.mark.parametrize("over, why", [
    ({"schema": "sa2-save-state/v0"}, "not a save-state pin"),
    ({"directory": "resource/gd_PC"}, "the game's save directory is resource/gd_PC/SAVEDATA"),
    ({"files": {"sub/SONIC2B__ALF": EMPTY}}, "is not a plain file name"),
    ({"files": {"..": EMPTY}}, "is not a plain file name"),
    ({"files": {"": EMPTY}}, "is not a plain file name"),
    ({"files": {"SONIC2B__ALF": EMPTY.upper()}}, "with a lower-case sha256"),
    ({"files": ["SONIC2B__ALF"]}, "files is not a map"),
    ({"digest": "0" * 64}, "its digest 000000000000 is not its files'"),
    ({"why": " "}, "says no why"),
    ({"state": None}, "says no state"),
    ({"restore": "cp -r somewhere private/install"}, "names the restore command"),
])
def test_a_pin_that_does_not_hold_together_is_refused(tmp_path, over, why):
    """Each case breaks one thing; new files come with their own digest, so
    the refusal is the files' and not the digest's."""
    doc = pin_doc({"SONIC2B__ALF": EMPTY})
    doc.update(over)
    if isinstance(over.get("files"), dict) and "digest" not in over:
        doc["digest"] = SS.digest(over["files"])
    path = tmp_path / "save-state.json"
    path.write_text(json.dumps(doc))
    with pytest.raises(SS.SaveStateError, match=why):
        SS.load(path)
    path.write_text("{")
    with pytest.raises(SS.SaveStateError, match="cannot be read"):
        SS.load(path)


# ---------------------------------------------------------------- the check

def test_the_check_takes_the_pin_exactly_and_names_every_difference(tmp_path):
    pin = pin_at(tmp_path, {"SONIC2B__ALF": EMPTY, "SONIC2B__S02": CHAO})
    install = install_with(tmp_path, {"SONIC2B__ALF": b"", "SONIC2B__S02": b"chao"})
    assert SS.problems(install, pin) == []
    saves = install / "resource" / "gd_PC" / "SAVEDATA"
    (saves / "SONIC2B__S01").write_bytes(b"")                   # the pickup's save
    (saves / "SONIC2B__ALF").write_bytes(b"x")                  # other bytes
    (saves / "SONIC2B__S02").unlink()                           # missing
    (saves / "sub").mkdir()                                     # not a file at all
    x = hashlib.sha256(b"x").hexdigest()
    assert SS.problems(install, pin) == [
        "missing SONIC2B__S02",
        "extra SONIC2B__S01 (e3b0c44298fc)", "extra sub (<dir>)",
        f"changed SONIC2B__ALF ({x[:12]} where the pin has e3b0c44298fc)"]
    with pytest.raises(SS.SaveStateError) as e:
        SS.require(install, pin)
    assert e.value.code.endswith(f"Restore them with `{SS.RESTORE}` from the repository root")


def test_an_installation_without_a_real_save_directory_is_not_the_pin(tmp_path):
    pin = pin_at(tmp_path, {})
    assert SS.problems(install_with(tmp_path, None), pin) == [
        "the installation has no directory resource/gd_PC/SAVEDATA"]
    linked = install_with(tmp_path, None, "linked")
    (tmp_path / "elsewhere").mkdir()
    os.symlink(tmp_path / "elsewhere", linked / "resource" / "gd_PC" / "SAVEDATA")
    assert SS.problems(linked, pin) == [
        "the installation's resource/gd_PC/SAVEDATA is a symlink"]
    assert SS.problems(install_with(tmp_path, {}, "empty"), pin) == []


# -------------------------------------------------------------- the restore

def test_restore_makes_the_save_directory_the_pin_and_writes_nothing_else(tmp_path):
    pin = pin_at(tmp_path, {"SONIC2B__ALF": EMPTY, "SONIC2B__S02": EMPTY, "KEPT": CHAO})
    install = install_with(tmp_path, {"SONIC2B__ALF": b"a save", "SONIC2B__S01": b"",
                                      "KEPT": b"chao"})
    saves = install / "resource" / "gd_PC" / "SAVEDATA"
    (saves / "sub").mkdir()
    (saves / "sub" / "deep").write_bytes(b"d")
    target = tmp_path / "not-a-save"
    target.write_bytes(b"never followed")
    os.symlink(target, saves / "link")
    before = outside_saves(install)
    kept = (saves / "KEPT").stat().st_mtime_ns
    assert SS.restore(install, pin) == [
        "removed SONIC2B__S01 (e3b0c44298fc)", "removed link (<symlink>)", "removed sub (<dir>)",
        "emptied SONIC2B__ALF (empty)", "created SONIC2B__S02 (empty)"]
    assert SS.problems(install, pin) == []
    assert target.read_bytes() == b"never followed"
    assert (saves / "KEPT").stat().st_mtime_ns == kept           # a file already right is left
    assert outside_saves(install) == before
    assert SS.restore(install, pin) == []                       # idempotent


def test_restore_refuses_bytes_it_does_not_have_and_changes_nothing(tmp_path):
    """Only an empty file can be written from its hash; a pinned save with
    content must come back from a copy."""
    pin = pin_at(tmp_path, {"SONIC2B__ALF": CHAO})
    install = install_with(tmp_path, {"SONIC2B__ALF": b"other", "SONIC2B__S01": b""})
    with pytest.raises(SS.SaveStateError, match="the pin wants SONIC2B__ALF with bytes this "
                                                "tool does not have"):
        SS.restore(install, pin)
    saves = install / "resource" / "gd_PC" / "SAVEDATA"
    assert sorted(p.name for p in saves.iterdir()) == ["SONIC2B__ALF", "SONIC2B__S01"]
    assert (saves / "SONIC2B__ALF").read_bytes() == b"other"


def test_restore_creates_a_missing_save_directory_and_nothing_above_it(tmp_path):
    pin = pin_at(tmp_path, {"SONIC2B__ALF": EMPTY})
    install = install_with(tmp_path, None)
    assert SS.restore(install, pin) == ["created the directory resource/gd_PC/SAVEDATA",
                                        "created SONIC2B__ALF (empty)"]
    assert SS.problems(install, pin) == []
    bare = tmp_path / "bare"
    bare.mkdir()
    with pytest.raises(SS.SaveStateError, match="has no directory resource"):
        SS.restore(bare, pin)
    assert list(bare.iterdir()) == []


def test_restore_never_writes_through_a_symlinked_save_directory(tmp_path):
    pin = pin_at(tmp_path, {"SONIC2B__ALF": EMPTY})
    install = install_with(tmp_path, None)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "SONIC2B__S01").write_bytes(b"s")
    os.symlink(elsewhere, install / "resource" / "gd_PC" / "SAVEDATA")
    with pytest.raises(SS.SaveStateError, match="is not a directory"):
        SS.restore(install, pin)
    assert sorted(p.name for p in elsewhere.iterdir()) == ["SONIC2B__S01"]


def test_the_command_line_checks_restores_and_digests(tmp_path, capsys):
    pin = pin_at(tmp_path, {"SONIC2B__ALF": EMPTY})
    install = install_with(tmp_path, {"SONIC2B__ALF": b"", "SONIC2B__S01": b""})
    args = ["--pin", str(pin.path), "--install", str(install)]
    assert SS.main(["check", *args]) == 1
    assert capsys.readouterr().out.startswith(
        f"SAVE_STATE_DIFFERS the saves in {install}/resource/gd_PC/SAVEDATA are not the pinned "
        "start state")
    assert SS.main(["restore", *args]) == 0
    assert capsys.readouterr().out == (
        f"  removed SONIC2B__S01 (e3b0c44298fc)\nSAVE_STATE_OK {pin.digest} restored: {install}\n")
    assert SS.main(["check", *args]) == 0
    assert SS.main(["digest", *args]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[-2:] == [f"SAVE_STATE_PIN {pin.digest} a made-up start",
                        f"SAVE_STATE_INSTALL {pin.digest} matches the pin"]


# ------------------------------------------------------------ the lifted side

def overlay_with(tmp_path: Path, entries: dict[str, bytes | None]) -> Path:
    """A write overlay holding `entries`: a path -> bytes for a file, None for
    a directory."""
    overlay = tmp_path / "vfsroot"
    overlay.mkdir()
    for rel, data in entries.items():
        p = overlay / rel
        if data is None:
            p.mkdir(parents=True, exist_ok=True)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
    return overlay


def test_the_overlay_loses_its_saves_and_the_empty_directories_on_the_way(tmp_path):
    """What the lifted replay of the first recording left: the pickup's empty
    save in the overlay's save directory. The save directory goes whole, with
    any deletion marker in it that would hide an installation save
    (runtime/sa2_files.c whiteouts), and each directory above it left empty."""
    pin = pin_at(tmp_path, {"SONIC2B__ALF": EMPTY})
    install = install_with(tmp_path, {"SONIC2B__ALF": b""})
    overlay = overlay_with(tmp_path, {"resource/gd_PC/SAVEDATA/SONIC2B__S01": b""})
    record = SS.reset_overlay(overlay, install, pin, root=tmp_path)
    assert record == {"pin": {"file": "save-state.json", "digest": pin.digest,
                              "state": "a made-up start"},
                      "installation": "install", "installation_matches_pin": True,
                      "overlay": "vfsroot",
                      "removed": {"resource/gd_PC/SAVEDATA/SONIC2B__S01": EMPTY,
                                  "resource/gd_PC/SAVEDATA": "<dir>",
                                  "resource/gd_PC": "<dir>", "resource": "<dir>"}}
    assert overlay.is_dir() and list(overlay.iterdir()) == []
    assert SS.reset_overlay(overlay, install, pin, root=tmp_path)["removed"] == {}
    assert SS.problems(install, pin) == []                       # the installation is read only


def test_the_overlay_keeps_everything_but_its_saves(tmp_path):
    """A file the guest wrote beside the save directory stays, and so does the
    directory holding it; the save directory goes under any spelling the
    guest's case-blind lookup would take for it."""
    pin = pin_at(tmp_path, {"SONIC2B__ALF": EMPTY})
    install = install_with(tmp_path, {"SONIC2B__ALF": b""})
    overlay = overlay_with(tmp_path, {"resource/gd_PC/copied.prs": b"c",
                                      "resource/gd_PC/SAVEDATA": None,
                                      "RESOURCE/gd_pc/savedata/SONIC2B__S01": b"s",
                                      "Config/UserConfig.cfg": b"cfg"})
    removed = SS.reset_overlay(overlay, install, pin, root=tmp_path)["removed"]
    assert removed == {"RESOURCE/gd_pc/savedata/SONIC2B__S01": hashlib.sha256(b"s").hexdigest(),
                       "RESOURCE/gd_pc/savedata": "<dir>", "resource/gd_PC/SAVEDATA": "<dir>",
                       "RESOURCE/gd_pc": "<dir>", "RESOURCE": "<dir>"}
    left = sorted(str(p.relative_to(overlay)) for p in overlay.rglob("*"))
    assert left == ["Config", "Config/UserConfig.cfg", "resource", "resource/gd_PC",
                    "resource/gd_PC/copied.prs"]


def test_the_overlay_is_left_alone_when_the_installation_is_not_the_pin(tmp_path):
    pin = pin_at(tmp_path, {"SONIC2B__ALF": EMPTY})
    install = install_with(tmp_path, {"SONIC2B__ALF": b"", "SONIC2B__S01": b""})
    overlay = overlay_with(tmp_path, {"resource/gd_PC/SAVEDATA/SONIC2B__S01": b""})
    with pytest.raises(SS.SaveStateError, match=r"extra SONIC2B__S01 \(e3b0c44298fc\)"):
        SS.reset_overlay(overlay, install, pin, root=tmp_path)
    assert (overlay / "resource/gd_PC/SAVEDATA/SONIC2B__S01").is_file()


def test_nothing_is_removed_through_a_symlink_in_the_overlay(tmp_path):
    pin = pin_at(tmp_path, {"SONIC2B__ALF": EMPTY})
    install = install_with(tmp_path, {"SONIC2B__ALF": b""})
    overlay = overlay_with(tmp_path, {"Config": None})
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "gd_PC" / "SAVEDATA").mkdir(parents=True)
    (elsewhere / "gd_PC" / "SAVEDATA" / "SONIC2B__S01").write_bytes(b"")
    os.symlink(elsewhere, overlay / "resource")
    with pytest.raises(SS.SaveStateError, match="resource in the write overlay is a symlink"):
        SS.reset_overlay(overlay, install, pin, root=tmp_path)
    assert (elsewhere / "gd_PC" / "SAVEDATA" / "SONIC2B__S01").is_file()
    assert SS.reset_overlay(tmp_path / "none-yet", install, pin, root=tmp_path)["removed"] == {}


def test_a_lifted_run_s_roots_are_its_command_s_over_loop_run_s_defaults(tmp_path):
    base = ["python", "tools/s4/loop_run.py", "--name", "n", "--out-prefix", "n"]
    spec = "a|SA2_PUMP_POLLS=10|SA2_REPLAY_TICKS=private/x.csv"
    assert SS.lifted_roots(base + ["--run", spec, "--hook", "h.c"], tmp_path) == (
        tmp_path / "private/install", tmp_path / "private/generated/s4/vfsroot")
    moved = spec + f"|SA2_VFS_WRITE_ROOT={tmp_path / 'other'}|SA2_INSTALL=elsewhere/install"
    assert SS.lifted_roots(base + ["--run", moved], tmp_path) == (
        tmp_path / "elsewhere/install", tmp_path / "other")
    for cmd in (base, base + ["--run", spec, "--run", spec], base + [f"--run={spec}"]):
        with pytest.raises(SS.SaveStateError, match="must carry one --run"):
            SS.lifted_roots(cmd, tmp_path)


def test_the_lifted_defaults_are_loop_run_s():
    """loop_run.py hands every run SA2_INSTALL and SA2_VFS_WRITE_ROOT unless
    its --run names others, and the runtime reads the same defaults
    (runtime/sa2_files.c sa2_vfs_config)."""
    sys.path.insert(0, str(HERE.parent / "s4"))
    import loop_run
    assert SS.LIFTED_DEFAULTS == loop_run.BASE_ENV
    runtime = (HERE.parents[1] / "runtime" / "sa2_files.c").read_text()
    assert 'rr = "private/install";' in runtime
    assert 'wr = "private/generated/s4/vfsroot";' in runtime


def test_reset_lifted_reads_the_pin_and_roots_under_the_root_it_is_given(tmp_path):
    (tmp_path / "Documentation").mkdir()
    (tmp_path / "Documentation" / "save-state.json").write_text(
        json.dumps(pin_doc({"SONIC2B__ALF": EMPTY})))
    install_with(tmp_path / "private", {"SONIC2B__ALF": b""})
    overlay = tmp_path / "private" / "generated" / "s4" / "vfsroot"
    (overlay / "resource" / "gd_PC" / "SAVEDATA").mkdir(parents=True)
    cmd = ["loop_run.py", "--run", "a|SA2_PUMP_POLLS=1"]
    record = SS.reset_lifted(cmd, tmp_path)
    assert record["pin"]["file"] == "Documentation/save-state.json"
    assert record["installation"] == "private/install"
    assert record["overlay"] == "private/generated/s4/vfsroot"
    assert list(record["removed"]) == ["resource/gd_PC/SAVEDATA", "resource/gd_PC", "resource"]


# ------------------------------------------------ what is never written (review)

def test_restore_never_writes_through_a_hard_link(tmp_path):
    """A pinned file that differs is made again, not written in place: a file
    outside the save directory that shares its bytes keeps them (the
    save-state review of 2026-09-24, W4)."""
    pin = pin_at(tmp_path, {"SONIC2B__ALF": EMPTY})
    install = install_with(tmp_path, {})
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"not a save")
    os.link(outside, install / "resource" / "gd_PC" / "SAVEDATA" / "SONIC2B__ALF")
    assert SS.restore(install, pin) == ["emptied SONIC2B__ALF (empty)"]
    assert outside.read_bytes() == b"not a save" and SS.problems(install, pin) == []
    assert os.stat(outside).st_nlink == 1


def test_restore_never_writes_through_a_linked_save_or_a_linked_directory_on_the_way(tmp_path):
    """A pinned save that is a symlink is replaced, its target left; a
    resource/gd_PC that is a symlink is refused before anything changes
    (review W5)."""
    pin = pin_at(tmp_path, {"SONIC2B__ALF": EMPTY})
    install = install_with(tmp_path, {})
    target = tmp_path / "target"
    target.write_bytes(b"kept")
    os.symlink(target, install / "resource" / "gd_PC" / "SAVEDATA" / "SONIC2B__ALF")
    assert SS.restore(install, pin) == ["emptied SONIC2B__ALF (empty)"]
    assert target.read_bytes() == b"kept" and SS.problems(install, pin) == []
    other = tmp_path / "other"
    (other / "resource").mkdir(parents=True)
    real_gd = tmp_path / "real_gd"
    (real_gd / "SAVEDATA").mkdir(parents=True)
    (real_gd / "SAVEDATA" / "SONIC2B__S01").write_bytes(b"s")
    os.symlink(real_gd, other / "resource" / "gd_PC")
    with pytest.raises(SS.SaveStateError, match="or it is a symlink"):
        SS.restore(other, pin)
    assert (real_gd / "SAVEDATA" / "SONIC2B__S01").read_bytes() == b"s"


def owned_steam(tmp_path, monkeypatch) -> Path:
    """A made-up home whose owned Steam installation holds a real save."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    steam = tmp_path / "home" / ".local/share/Steam/steamapps/common/Sonic Adventure 2"
    (steam / "resource" / "gd_PC" / "SAVEDATA").mkdir(parents=True)
    (steam / "resource" / "gd_PC" / "SAVEDATA" / "SONIC2B__S01").write_bytes(b"a real save")
    (steam / "sonic2app.exe").write_bytes(b"MZ")
    assert SS.owned_installation() == steam
    return steam


def test_restore_refuses_the_owned_steam_installation(tmp_path, monkeypatch):
    """By its path, through a link to it, or below it: nothing here writes
    the user's own installation (AGENTS.md rule 11; review W1)."""
    steam = owned_steam(tmp_path, monkeypatch)
    pin = pin_at(tmp_path, {"SONIC2B__ALF": EMPTY})
    os.symlink(steam, tmp_path / "looks-staged")
    below = steam / "mods" / "copy"                      # an installation-shaped tree below it
    (below / "resource" / "gd_PC" / "SAVEDATA").mkdir(parents=True)
    (below / "resource" / "gd_PC" / "SAVEDATA" / "SONIC2B__S01").write_bytes(b"its save")
    for install in (steam, tmp_path / "looks-staged", steam / "resource" / "..", below):
        with pytest.raises(SS.SaveStateError, match="owned Steam installation"):
            SS.restore(install, pin)
    assert (steam / "resource/gd_PC/SAVEDATA/SONIC2B__S01").read_bytes() == b"a real save"
    assert (below / "resource/gd_PC/SAVEDATA/SONIC2B__S01").read_bytes() == b"its save"
    staged = install_with(tmp_path, {"SONIC2B__S01": b""})       # a staged copy is restored
    assert SS.restore(staged, pin) == ["removed SONIC2B__S01 (e3b0c44298fc)",
                                       "created SONIC2B__ALF (empty)"]


def test_the_overlay_reset_refuses_a_path_that_is_no_write_overlay(tmp_path, monkeypatch):
    """Its save directory is what the reset removes, so an installation, the
    owned one, or anything holding the game's executable is refused, and
    nothing is removed from it."""
    steam = owned_steam(tmp_path, monkeypatch)
    pin = pin_at(tmp_path, {"SONIC2B__ALF": EMPTY})
    install = install_with(tmp_path, {"SONIC2B__ALF": b""})
    mirror = overlay_with(tmp_path, {"SONIC2APP-unpacked.EXE": b"MZ",
                                     "resource/gd_PC/SAVEDATA/SONIC2B__ALF": b""})
    below = steam / "mods" / "overlay"
    (below / "resource" / "gd_PC" / "SAVEDATA").mkdir(parents=True)
    (below / "resource" / "gd_PC" / "SAVEDATA" / "SONIC2B__S01").write_bytes(b"its save")
    for overlay, why in ((install, "is the installation itself"),
                         (steam, "is in the owned Steam installation"),
                         (below, "is in the owned Steam installation"),
                         (mirror, "holds the game's executable")):
        with pytest.raises(SS.SaveStateError, match=why):
            SS.reset_overlay(overlay, install, pin, root=tmp_path)
    assert (install / "resource/gd_PC/SAVEDATA/SONIC2B__ALF").is_file()
    assert (steam / "resource/gd_PC/SAVEDATA/SONIC2B__S01").read_bytes() == b"a real save"
    assert (below / "resource/gd_PC/SAVEDATA/SONIC2B__S01").read_bytes() == b"its save"
    assert (mirror / "resource/gd_PC/SAVEDATA/SONIC2B__ALF").is_file()
    assert SS.main(["reset-overlay", "--pin", str(pin.path), "--install", str(install),
                    "--overlay", str(install)]) == 1


def test_reset_lifted_clears_the_overlay_its_command_names_and_no_other(tmp_path):
    """A run whose --run names its own write root and installation is reset
    there, and the default overlay is left as it was (review N7, S14)."""
    (tmp_path / "Documentation").mkdir()
    (tmp_path / "Documentation" / "save-state.json").write_text(
        json.dumps(pin_doc({"SONIC2B__ALF": EMPTY})))
    install_with(tmp_path / "elsewhere", {"SONIC2B__ALF": b""})
    default = tmp_path / "private" / "generated" / "s4" / "vfsroot"
    (default / "resource" / "gd_PC" / "SAVEDATA").mkdir(parents=True)
    own = tmp_path / "own-root"
    (own / "resource" / "gd_PC" / "SAVEDATA").mkdir(parents=True)
    (own / "resource" / "gd_PC" / "SAVEDATA" / "SONIC2B__S01").write_bytes(b"")
    cmd = ["loop_run.py", "--run",
           f"a|SA2_VFS_WRITE_ROOT={own}|SA2_INSTALL=elsewhere/install"]
    record = SS.reset_lifted(cmd, tmp_path)
    assert record["installation"] == "elsewhere/install" and record["overlay"] == "own-root"
    assert record["removed"]["resource/gd_PC/SAVEDATA/SONIC2B__S01"] == EMPTY
    assert not (own / "resource").exists()
    assert (default / "resource" / "gd_PC" / "SAVEDATA").is_dir()


def test_a_native_capture_stands_for_a_lifted_replay_only_from_the_same_pin(tmp_path):
    """The lifted replay starts from root's pin; its native capture must have
    started there too, as its receipt records (review N5)."""
    (tmp_path / "Documentation").mkdir()
    pin_file = tmp_path / "Documentation" / "save-state.json"
    pin_file.write_text(json.dumps(pin_doc({"SONIC2B__ALF": EMPTY})))
    capture = tmp_path / "captures" / "c"
    capture.mkdir(parents=True)
    assert SS.native_start_problems(capture, tmp_path) == []       # no receipt: the caller's

    def receipt(saves):
        (capture / "receipt.json").write_text(json.dumps({"session": {"saves": saves}}))

    receipt({"at_start": {"SONIC2B__ALF": EMPTY}})
    assert SS.native_start_problems(capture, tmp_path) == []
    receipt({"at_start": {"SONIC2B__ALF": EMPTY, "SONIC2B__S01": EMPTY}})
    left = SS.digest({"SONIC2B__ALF": EMPTY, "SONIC2B__S01": EMPTY})
    assert SS.native_start_problems(capture, tmp_path) == [
        f"captures/c started from saves {left[:12]}, and a lifted replay of it starts from the "
        f"pin {SS.digest({'SONIC2B__ALF': EMPTY})[:12]} (Documentation/save-state.json, "
        "a made-up start)"]
    receipt({"at_start": {"SONIC2B__ALF": "b" * 64}})               # the same names, other bytes
    [problem] = SS.native_start_problems(capture, tmp_path)
    assert problem.startswith("captures/c started from saves ")
    receipt(None)
    [problem] = SS.native_start_problems(capture, tmp_path)
    assert problem.startswith("captures/c's receipt records no start saves")
    pin_file.write_text("{")
    [problem] = SS.native_start_problems(capture, tmp_path)
    assert "cannot be read" in problem
