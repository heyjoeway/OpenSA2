#!/usr/bin/env python3
"""Build a writable mirror of the staged installation that shares its bulk.

Subtitle: run the game somewhere the game cannot write the user's copy.

`private/install` is the user's staged installation and is read-only to this
project. The Mod Loader, however, needs to write: it appends
`mods/SA2ModLoader.log`, every probe writes its CSV into `mods/`, one probe
wrote a CSV into the installation root, and the Mod Loader profile index has
to name the temporary profile. The predecessor's runner solved that by
writing the installation and restoring it afterwards. This solves it by not
writing the installation at all.

The mirror is a real directory tree in which:

  * every directory that the game may write into (the root, `mods`, `Config`,
    `CrashDump`) is a real directory whose small files are copies;
  * the game's save directory, `resource/gd_PC/SAVEDATA` (SAVE_DIR), is a
    copy, and the two directories on the way to it, `resource` and
    `resource/gd_PC`, are real directories whose every other entry is a
    symlink to the installation's (`link_along`), so the 3 GB of shipped data
    in them is still shared and not copied. Until 2026-09-23 `resource` was one
    symlink, and the save the game writes when a stage's upgrade is picked up
    landed in the installation. An installation without a real save directory
    gets `resource` as one symlink here; the runner refuses one
    (`build(require_save_dir=True)`);
  * every other directory (`redist`, `extlib`) is one symlink;
  * every file at or above --symlink-threshold (default 2 MiB) is a symlink,
    because the large files are the executables and the shipped archives and
    nothing writes them.

A write to a copied file lands in the mirror. A write to a symlinked file
would reach the installation, so the mirror is only trustworthy together with
`tree_snapshot.py`, which is what actually proves the installation was not
touched. The two are separate on purpose: the mirror is the intent and the
snapshot is the evidence.

The save directory and the path to it are the exception, and each has its own
check, because the installation's snapshot cannot see them. A write inside the
save directory stays in the mirror, and the runner puts the installation's
saves back before every run (`reset_save_dir`), so every run starts from the
same saves. An entry created, removed or replaced anywhere else along the
path, in the mirror's `resource` or `resource/gd_PC`, would reach neither the
snapshot nor the reset and would carry into every later run. So the runner
compares the path with the installation before and after every run
(`save_path_problems`).

Usage:
  mirror_install.py <install-dir> <mirror-dir> [--symlink-threshold BYTES]
                    [--force]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

# Directories the game or the Mod Loader is known to create files in. Anything
# not listed is shared by a single directory symlink, except `resource` (below).
WRITABLE_DIRS = ("mods", "Config", "CrashDump")
DEFAULT_SYMLINK_THRESHOLD = 2 * 1024 * 1024
# The game's save directory. The game writes a save into it when a stage's
# upgrade is picked up (the user's first recording, 2026-09-23, took Metal
# Harbor's light dash and created SONIC2B__S01), and through the one symlink
# `resource` then was, that write landed in the staged installation. So the
# path to it is made of real directories whose other entries are symlinks, the
# directory itself is a copy, and `reset_save_dir` puts the installation's
# contents back before every run: each run starts from the same saves and none
# reaches the installation.
SAVE_DIR = ("resource", "gd_PC", "SAVEDATA")


def save_dir_problem(install: Path) -> str | None:
    """Why `install` has no save directory the mirror can copy, or None: each
    element of SAVE_DIR must be a real directory, not a symlink, spelled as
    the game spells it."""
    here = install.resolve()
    for part in SAVE_DIR:
        here = here / part
        if here.is_symlink():
            return f"the installation's {here.relative_to(install.resolve())} is a symlink"
        if not here.is_dir():
            return f"the installation has no directory {here.relative_to(install.resolve())}"
    return None


def save_files(directory: Path) -> list[Path]:
    """A save directory's entries, every one a regular file, or SystemExit
    naming the first that is not. The game writes only files there, and a
    symlink would be copied as a symlink, through which a run's write would
    reach whatever it names (the review of 2026-09-23, N1)."""
    out = []
    for entry in sorted(directory.iterdir()):
        if entry.is_symlink() or not entry.is_file():
            raise SystemExit(f"{entry} is not a regular file; a save directory holds only the "
                             "game's save files, and nothing else is copied into the mirror")
        out.append(entry)
    return out


def copy_save_dir(source: Path, destination: Path) -> None:
    """`source`, a save directory of regular files, copied to `destination`
    with each file's bytes, mode and times and the directory's own."""
    save_files(source)
    shutil.copytree(source, destination, symlinks=True)


def link_along(source: Path, destination: Path, path: tuple, stats: dict) -> None:
    """`destination` as a real directory whose entries are symlinks to
    `source`'s, except the one named path[0]: that is copied whole when it is
    the last element, and linked along the rest of `path` otherwise."""
    destination.mkdir(parents=True, exist_ok=True)
    stats["dirs_created"] += 1
    for entry in sorted(source.iterdir()):
        target = destination / entry.name
        if entry.name == path[0] and entry.is_dir() and not entry.is_symlink():
            if len(path) == 1:
                copy_save_dir(entry, target)
                stats["dirs_copied"] = stats.get("dirs_copied", 0) + 1
            else:
                link_along(entry, target, path[1:], stats)
        elif entry.is_symlink():
            os.symlink(os.readlink(entry), target)
            stats["symlinks_copied"] += 1
        elif entry.is_dir():
            os.symlink(entry.resolve(), target)
            stats["dirs_symlinked"] += 1
        else:
            os.symlink(entry.resolve(), target)
            stats["files_symlinked"] += 1
            stats["bytes_symlinked"] += entry.stat().st_size


def save_dir_is_private(mirror: Path) -> bool:
    """Whether the mirror's save directory, and every directory on the way to
    it, is the mirror's own, so a write there cannot reach the installation."""
    here = mirror
    for part in SAVE_DIR:
        here = here / part
        if here.is_symlink() or not here.is_dir():
            return False
    return True


def save_path_problems(install: Path, mirror: Path) -> list[str]:
    """Every entry of the mirror's directories on the way to SAVE_DIR that is
    not what `link_along` made from the installation: created, removed, or no
    longer the symlink it was. [] for a mirror as built. A run's write there
    reaches neither the installation's snapshot nor `reset_save_dir`, and an
    installation that gained or lost an entry there leaves the mirror stale, so
    the runner checks this before a run (and rebuilds) and after it (and
    refuses the run). A mirror whose save directory is not private has no such
    path, and is the caller's to rebuild (`save_dir_is_private`)."""
    problems = []
    src, dst = install.resolve(), mirror
    for depth, part in enumerate(SAVE_DIR[:-1]):
        src, dst = src / part, dst / part
        keep = SAVE_DIR[depth + 1]
        want = {e.name: e for e in src.iterdir()}
        have = {e.name: e for e in dst.iterdir()}
        rel = "/".join(SAVE_DIR[:depth + 1])
        problems += [f"created {rel}/{n}" for n in sorted(set(have) - set(want))]
        problems += [f"removed {rel}/{n}" for n in sorted(set(want) - set(have))]
        for n in sorted(set(want) & set(have)):
            if n == keep:
                continue
            e, m = want[n], have[n]
            expected = os.readlink(e) if e.is_symlink() else str(e.resolve())
            if not m.is_symlink() or os.readlink(m) != expected:
                problems.append(f"replaced {rel}/{n}")
    return problems


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def saves_of(directory: Path) -> dict[str, str]:
    """A save directory's contents, name -> sha256 of a regular file, or what
    the entry is instead ("<dir>", "<symlink>", "<other>"); {} for a missing
    directory. What a receipt records of the saves a run started and ended
    with."""
    if not directory.is_dir():
        return {}
    out = {}
    for entry in sorted(directory.iterdir()):
        if entry.is_symlink():
            out[entry.name] = "<symlink>"
        elif entry.is_dir():
            out[entry.name] = "<dir>"
        elif entry.is_file():
            out[entry.name] = _file_sha256(entry)
        else:
            out[entry.name] = "<other>"
    return out


def save_differences(install: Path, mirror: Path) -> list[str]:
    """How the mirror's save directory differs from the installation's, one
    line per entry: created, removed, modified (other bytes or another kind of
    entry) or re-permissioned."""
    src, dst = install.joinpath(*SAVE_DIR), mirror.joinpath(*SAVE_DIR)
    want = {p.name: p for p in save_files(src)}
    have = {p.name: p for p in dst.iterdir()}
    out = [f"created {n}" for n in sorted(set(have) - set(want))]
    out += [f"removed {n}" for n in sorted(set(want) - set(have))]
    for n in sorted(set(want) & set(have)):
        m = have[n]
        if m.is_symlink() or not m.is_file() or _file_sha256(m) != _file_sha256(want[n]):
            out.append(f"modified {n}")
        elif (m.stat().st_mode & 0o7777) != (want[n].stat().st_mode & 0o7777):
            out.append(f"re-permissioned {n}")
    return out


def reset_save_dir(install: Path, mirror: Path) -> list[str]:
    """Put the installation's save directory back into the mirror's: remove
    whatever a previous run did there and copy the installation's files, each
    with its bytes, mode and times. Returns what the reset undid
    (`save_differences`), [] when the mirror's copy was already the
    installation's."""
    if not save_dir_is_private(mirror):
        raise SystemExit(f"the mirror's {'/'.join(SAVE_DIR)} is shared with the installation; "
                         "rebuild the mirror")
    src, dst = install.joinpath(*SAVE_DIR), mirror.joinpath(*SAVE_DIR)
    undone = save_differences(install, mirror)
    shutil.rmtree(dst)
    copy_save_dir(src, dst)
    return undone


def mirror_dir(source: Path, destination: Path, threshold: int, stats: dict) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    stats["dirs_created"] += 1
    for entry in sorted(source.iterdir()):
        target = destination / entry.name
        if entry.is_symlink():
            os.symlink(os.readlink(entry), target)
            stats["symlinks_copied"] += 1
        elif entry.is_dir():
            mirror_dir(entry, target, threshold, stats)
        elif entry.is_file():
            if entry.stat().st_size >= threshold:
                os.symlink(entry.resolve(), target)
                stats["files_symlinked"] += 1
                stats["bytes_symlinked"] += entry.stat().st_size
            else:
                shutil.copy2(entry, target)
                stats["files_copied"] += 1
                stats["bytes_copied"] += entry.stat().st_size
        else:
            stats["skipped"].append(entry.name)


def build(install: Path, mirror: Path, threshold: int, force: bool,
          require_save_dir: bool = False) -> dict:
    """The mirror of `install` at `mirror`, and what was copied and shared.

    `require_save_dir` is the runner's: an installation without a real save
    directory would get `resource` as one symlink, the runner would find the
    save directory shared and rebuild on every call, and the reset would refuse
    each time with advice no rebuild can follow. So it is refused here, naming
    the element that is missing or a symlink (the review of 2026-09-23, N5)."""
    install = install.resolve()
    if not (install / "sonic2app-unpacked.exe").is_file():
        raise SystemExit(f"not an SA2 installation (no sonic2app-unpacked.exe): {install}")
    owned = Path.home() / ".local/share/Steam/steamapps/common/Sonic Adventure 2"
    if owned.exists() and install == owned.resolve():
        raise SystemExit("refusing to mirror the owned Steam installation")
    missing_saves = save_dir_problem(install)
    if require_save_dir and missing_saves:
        raise SystemExit(f"{missing_saves}, so the mirror cannot give runs a save directory of "
                         "their own and a save would reach the installation")
    if not missing_saves:
        save_files(install.joinpath(*SAVE_DIR))     # refused here, before any mirror is removed
    if mirror.exists():
        if not force:
            raise SystemExit(f"mirror already exists (pass --force to replace): {mirror}")
        if not (mirror / ".sa2-oracle-mirror").is_file():
            raise SystemExit(
                f"refusing to replace {mirror}: it is not a mirror this tool built"
            )
        shutil.rmtree(mirror)
    stats = {
        "dirs_created": 0,
        "dirs_symlinked": 0,
        "files_copied": 0,
        "files_symlinked": 0,
        "symlinks_copied": 0,
        "bytes_copied": 0,
        "bytes_symlinked": 0,
        "skipped": [],
    }
    mirror.mkdir(parents=True)
    stats["dirs_created"] += 1
    for entry in sorted(install.iterdir()):
        target = mirror / entry.name
        if entry.is_symlink():
            os.symlink(os.readlink(entry), target)
            stats["symlinks_copied"] += 1
        elif entry.is_dir():
            if entry.name in WRITABLE_DIRS:
                mirror_dir(entry, target, threshold, stats)
            elif entry.name == SAVE_DIR[0] and not missing_saves:
                link_along(entry, target, SAVE_DIR[1:], stats)
            else:
                os.symlink(entry.resolve(), target)
                stats["dirs_symlinked"] += 1
        elif entry.is_file():
            if entry.stat().st_size >= threshold:
                os.symlink(entry.resolve(), target)
                stats["files_symlinked"] += 1
                stats["bytes_symlinked"] += entry.stat().st_size
            else:
                shutil.copy2(entry, target)
                stats["files_copied"] += 1
                stats["bytes_copied"] += entry.stat().st_size
    (mirror / ".sa2-oracle-mirror").write_text(
        json.dumps({"install": str(install), "threshold": threshold}) + "\n"
    )
    for required in ("sonic2app-unpacked.exe", "SA2ModLoader.dll", "mods/.modloader"):
        if not (mirror / required).exists():
            raise SystemExit(f"mirror is missing {required}")
    if stats["files_copied"] == 0:
        raise SystemExit("mirror copied no file; refusing a mirror that shares everything")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("install", type=Path)
    parser.add_argument("mirror", type=Path)
    parser.add_argument("--symlink-threshold", type=int, default=DEFAULT_SYMLINK_THRESHOLD)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    stats = build(args.install, args.mirror, args.symlink_threshold, args.force)
    print(
        "SA2_ORACLE_MIRROR "
        f"mirror={args.mirror} copied={stats['files_copied']} "
        f"({stats['bytes_copied']} B) symlinked_files={stats['files_symlinked']} "
        f"({stats['bytes_symlinked']} B) symlinked_dirs={stats['dirs_symlinked']} "
        f"dirs={stats['dirs_created']}"
    )
    json.dump(stats, sys.stdout, indent=1, sort_keys=True)
    print()


if __name__ == "__main__":
    main()
