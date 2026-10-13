#!/usr/bin/env python3
"""The saves every native and lifted run starts from: pinned, checked, restored.

Subtitle: the save file as a recorded input.

    source tools/env.sh
    .venv/bin/python tools/oracle/save_state.py check           # the installation against the pin
    .venv/bin/python tools/oracle/save_state.py restore         # make it the pin
    .venv/bin/python tools/oracle/save_state.py digest          # both digests
    .venv/bin/python tools/oracle/save_state.py reset-overlay   # clear the lifted overlay's saves

Why this exists
---------------
The game writes its save directory, `resource/gd_PC/SAVEDATA`, when a stage's
upgrade is picked up, and a run that starts from other saves can play another
game. On 2026-09-24 the user ruled: "treat the save file as a recorded input.
Restore the staged install's save to a fixed starting state, record its hash
in the capture's receipt, and restore it before every native and lifted
replay."

The pin is `Documentation/save-state.json`: the save directory, each file's
name and sha256, a digest of the whole state, which state it is and why, and
the command that restores it. It holds names and hashes only, because save
files are the user's data. The digest is the sha256 of the {name: sha256} map
written as JSON with sorted keys and no spaces (`digest`).

The native side. `tools/oracle/run_oracle_probe.py` refuses to start unless
the staged installation's save directory is the pin, and each receipt records
the pin's digest and whether the saves the run started from match it
(`session.saves`). The mirror copies the installation's saves in before every
run (`tools/oracle/mirror_install.py reset_save_dir`), so a pinned
installation gives every run the pinned saves. `tools/oracle/record_stage.py`
holds a recording and every replay of it to the pin.

The lifted side. The guest reads a file from the write overlay first and the
installation second (`runtime/sa2_files.c`, map_read). A search of the save
directory lists the two merged, the overlay winning a name
(`sa2_vfs_find_first`; every other directory lists the one layer the map
chose), and deleting a save the installation holds leaves a marker,
`.sa2-whiteout.<save>`, in the overlay's save directory, which hides the
installation's copy. So a save the lifted game wrote or deleted stays that way
in the overlay for every later run. `reset_overlay` removes the overlay's
save directory, and each directory on the way to it that is then empty, after
checking that the installation is the pin. `tools/s4/stage_run.py`,
`tools/s4/demo_run.py` and `tools/s4/tick_replay.py`'s lifted run call it
before each run (`reset_lifted`) and keep what it removed in their records,
with what the overlay still holds after it (`left`); the reach run and the
boot replay's hooked run, which are compared field for field, refuse to start
unless that is nothing (`require_empty`). The stage replay, the attract demo
and the boot replay refuse a native capture whose receipt does not show it
started from the same pin (`native_start_problems`).

What the pin does not cover: file modes and times. The mirror copies the
installation's with each file, and the lifted build reads the installation's.
A restore that has to re-create a file gives it the current time.

What this tool never writes: the user's own Steam installation, which the
rules forbid (AGENTS.md rule 11, and tools/oracle/mirror_install.py build
refuses it too), and anything through a link. `restore` refuses the owned
installation, re-creates a file it must change rather than writing it in
place, so a hard link carries no write out of the save directory, and
removes a symlink rather than following it. `reset_overlay` refuses an
overlay that is the installation, the owned installation, or holds the
game's executable, since a write overlay holds only what the lifted game
wrote (the save-state review of 2026-09-24, W1 and W4).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
from mirror_install import SAVE_DIR, save_dir_problem, saves_of  # noqa: E402

PIN = ROOT / "Documentation" / "save-state.json"
SCHEMA = "sa2-save-state/v1"
INSTALL = ROOT / "private" / "install"
# The installation and the write overlay a lifted run gets unless its --run
# names others: tools/s4/loop_run.py BASE_ENV, relative to the repository root
# it runs the guest in (tools/oracle/test_save_state.py holds the two equal).
LIFTED_DEFAULTS = {"SA2_INSTALL": "private/install",
                   "SA2_VFS_WRITE_ROOT": "private/generated/s4/vfsroot"}
OVERLAY = ROOT / LIFTED_DEFAULTS["SA2_VFS_WRITE_ROOT"]
EMPTY = hashlib.sha256(b"").hexdigest()
RESTORE = ".venv/bin/python tools/oracle/save_state.py restore"
_HEX = re.compile(r"[0-9a-f]{64}\Z")
# What an installation or a mirror holds at its root and a write overlay never
# does: the game (tools/oracle/mirror_install.py requires the unpacked one).
EXECUTABLES = ("sonic2app.exe", "sonic2app-unpacked.exe")


class SaveStateError(SystemExit):
    """A pin that cannot be read, or saves that are not the pin."""


@dataclass(frozen=True)
class Pin:
    path: Path
    files: dict        # name -> sha256
    digest: str
    state: str

    def record(self, root: Path = ROOT) -> dict:
        """What a receipt or a run record keeps of the pin."""
        return {"file": rel(self.path, root), "digest": self.digest, "state": self.state}


def rel(path: Path, root: Path = ROOT) -> str:
    try:
        return str(Path(path).relative_to(root))
    except ValueError:
        return str(path)


def digest(files: dict) -> str:
    """The digest of a save state, {name: sha256}: the sha256 of the map as
    JSON with sorted keys and no spaces. Two states with the same names and
    bytes have the same digest; anything else a different one."""
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":"))
                          .encode()).hexdigest()


def load(path: Path | None = None) -> Pin:
    """The pin (PIN unless `path`), checked before anything uses it: its
    schema; the save directory mirror_install.SAVE_DIR names; each file a
    plain name with a sha256; a digest that is its files'; a state and a
    reason; and this tool's restore command. A pin that fails any of these is
    refused, never read as empty."""
    path = PIN if path is None else Path(path)
    where = rel(path)
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise SaveStateError(f"{where}: the save-state pin cannot be read: {e}") from None
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise SaveStateError(f"{where}: not a save-state pin (schema {SCHEMA})")
    if doc.get("directory") != "/".join(SAVE_DIR):
        raise SaveStateError(f"{where}: pins {doc.get('directory')!r}, and the game's save "
                             f"directory is {'/'.join(SAVE_DIR)}")
    files = doc.get("files")
    if not isinstance(files, dict):
        raise SaveStateError(f"{where}: files is not a map of name to sha256")
    for name, sha in files.items():
        if (not name or name in (".", "..") or "/" in name or "\\" in name
                or not isinstance(sha, str) or not _HEX.match(sha)):
            raise SaveStateError(f"{where}: {name!r} -> {sha!r} is not a plain file name with a "
                                 "lower-case sha256")
    if doc.get("digest") != digest(files):
        raise SaveStateError(f"{where}: its digest {str(doc.get('digest'))[:12]} is not its "
                             f"files' ({digest(files)[:12]})")
    for key in ("state", "why"):
        if not isinstance(doc.get(key), str) or not doc[key].strip():
            raise SaveStateError(f"{where}: says no {key}; a pinned state without one is a "
                                 "number without a reason")
    if doc.get("restore") != RESTORE:
        raise SaveStateError(f"{where}: names the restore command {doc.get('restore')!r}, and "
                             f"this tool's is {RESTORE!r}")
    return Pin(Path(path), dict(files), doc["digest"], doc["state"])


def state(install: Path) -> dict:
    """`install`'s save directory as a state: name -> sha256 of a regular
    file, or what the entry is instead ("<dir>", "<symlink>", "<other>");
    {} when there is no directory (mirror_install.saves_of)."""
    return saves_of(Path(install).joinpath(*SAVE_DIR))


def _short(what: str) -> str:
    return what[:12] if _HEX.match(what) else what


def problems(install: Path, pin: Pin) -> list[str]:
    """Every way `install`'s save directory differs from the pin, by name and
    hash; [] when it is the pin exactly: the same names, the same bytes,
    nothing extra, and a real directory on a path of real directories."""
    where = save_dir_problem(Path(install))
    if where:
        return [where]
    have, want = state(install), pin.files
    out = [f"missing {n}" for n in sorted(set(want) - set(have))]
    out += [f"extra {n} ({_short(have[n])})" for n in sorted(set(have) - set(want))]
    out += [f"changed {n} ({_short(have[n])} where the pin has {_short(want[n])})"
            for n in sorted(set(have) & set(want)) if have[n] != want[n]]
    return out


def refusal(install: Path, pin: Pin, wrong: list[str]) -> str:
    return (f"the saves in {rel(Path(install))}/{'/'.join(SAVE_DIR)} are not the pinned start "
            f"state ({rel(pin.path)}, {pin.state}): {'; '.join(wrong)}. Restore them with "
            f"`{RESTORE}` from the repository root")


def require(install: Path, pin: Pin) -> None:
    """SaveStateError naming each difference and the restore command, unless
    `install`'s saves are the pin."""
    wrong = problems(install, pin)
    if wrong:
        raise SaveStateError(refusal(install, pin, wrong))


def owned_installation() -> Path:
    """The user's own Steam installation, which nothing here writes: the
    path tools/oracle/mirror_install.py build refuses, read at call time."""
    return Path.home() / ".local/share/Steam/steamapps/common/Sonic Adventure 2"


def _within_owned(path: Path) -> bool:
    """Whether `path`, links resolved, is the owned installation or under it."""
    owned = owned_installation()
    if not owned.exists():
        return False
    owned, path = owned.resolve(), Path(path).resolve()
    return path == owned or owned in path.parents


def _remove(entry: Path) -> None:
    """An entry removed without following a symlink."""
    if entry.is_dir() and not entry.is_symlink():
        shutil.rmtree(entry)
    else:
        entry.unlink()


def restore(install: Path, pin: Pin) -> list[str]:
    """Make `install`'s save directory the pin and say what was done; [] when
    it already was. Only that directory is written: an extra entry is removed
    (a symlink is removed, never followed), and a pinned file that is missing
    or differs is made, which this tool can do only for an empty one. A
    pinned file with bytes it does not have is refused before anything
    changes, as is a save directory, or a directory on the way to it, that is
    a symlink or missing (the directory itself is created), and the user's own
    Steam installation (owned_installation). A pinned file that differs is
    removed and made again, never written in place, so no link to it, hard or
    symbolic, carries the write anywhere else."""
    install = Path(install)
    if _within_owned(install):
        raise SaveStateError(f"refusing to restore the saves of the owned Steam installation "
                             f"({rel(install)}): nothing here writes it; nothing was changed")
    parent = install.resolve()
    for part in SAVE_DIR[:-1]:
        parent = parent / part
        if parent.is_symlink() or not parent.is_dir():
            where = rel(parent, install.resolve())
            raise SaveStateError(f"{rel(install)} has no directory {where} (or it is a symlink), "
                                 "so it is not the installation this restores; nothing was "
                                 "changed")
    target = parent / SAVE_DIR[-1]
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise SaveStateError(f"{rel(install)}'s {'/'.join(SAVE_DIR)} is not a directory; nothing "
                             "was changed")
    have = state(install)
    want = pin.files
    unavailable = [n for n in sorted(want) if have.get(n) != want[n] and want[n] != EMPTY]
    if unavailable:
        raise SaveStateError(f"the pin wants {', '.join(unavailable)} with bytes this tool does "
                             "not have (only an empty file can be written from its hash); put "
                             f"them back from a copy, then check again. Nothing was changed")
    done = []
    if not target.is_dir():
        target.mkdir()
        done.append(f"created the directory {'/'.join(SAVE_DIR)}")
    for n in sorted(set(have) - set(want)):
        _remove(target / n)
        done.append(f"removed {n} ({_short(have[n])})")
    for n in sorted(want):
        if have.get(n) == want[n]:
            continue
        entry = target / n
        if n in have:               # made again, never written in place: a link would carry it out
            _remove(entry)
        os.close(os.open(entry, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o664))
        done.append(f"{'emptied' if n in have else 'created'} {n} (empty)")
    left = problems(install, pin)
    if left:
        raise SaveStateError(f"after restoring, {rel(install)} still differs from the pin: "
                             f"{'; '.join(left)}")
    return done


# ------------------------------------------------------------- lifted side

def _fold(name: str) -> str:
    """ASCII case folding, the runtime's rule (runtime/sa2_files.c)."""
    return "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in name)


def _named(directory: Path, name: str) -> list[Path]:
    """`directory`'s entries named `name`, case-blind as the guest names them."""
    if directory.is_symlink() or not directory.is_dir():
        return []
    return sorted(e for e in directory.iterdir() if _fold(e.name) == _fold(name))


def _contents(top: Path, base: Path) -> dict:
    """Every entry at and under `top`, deepest first, as path relative to
    `base` -> sha256 of a regular file, "<dir>", "<symlink>" or "<other>"."""
    out: dict = {}
    if top.is_dir() and not top.is_symlink():
        for entry in sorted(top.iterdir()):
            out.update(_contents(entry, base))
    what = ("<symlink>" if top.is_symlink() else "<dir>" if top.is_dir()
            else hashlib.sha256(top.read_bytes()).hexdigest() if top.is_file() else "<other>")
    out[rel(top, base)] = what
    return out


def overlay_problem(overlay: Path, install: Path) -> str | None:
    """Why `overlay` cannot be a lifted run's write overlay, or None: it is the
    installation, or in the owned Steam installation, or holds the game's
    executable at its root, as an installation or the oracle's mirror does and
    a write overlay never does. Its save directory is what reset_overlay
    removes, so a wrong path here would remove real saves."""
    if not overlay.exists():
        return None
    if overlay.resolve() == Path(install).resolve():
        return "is the installation itself, not a write overlay"
    if _within_owned(overlay):
        return "is in the owned Steam installation, which nothing here writes"
    if overlay.is_dir() and any(_named(overlay, exe) for exe in EXECUTABLES):
        return ("holds the game's executable, as an installation or a mirror does and a write "
                "overlay never does")
    return None


def reset_overlay(overlay: Path, install: Path, pin: Pin, root: Path = ROOT) -> dict:
    """Give a lifted run the pinned saves: `install` must be the pin, and the
    write `overlay` then loses its save directory, under any spelling the
    guest's case-blind lookup would take for it, and each directory on the way
    to it that is left empty. Returns the run record's `saves`: the pin, and
    every entry removed, path -> sha256 or what it was; {} removed when the
    overlay had none. Refuses, before removing anything, an installation that
    is not the pin and an overlay whose path to the save directory has a
    symlink on it, since nothing is removed through one, and a path that is no
    write overlay (overlay_problem)."""
    require(install, pin)
    overlay = Path(overlay)
    wrong = overlay_problem(overlay, install)
    if wrong:
        raise SaveStateError(f"{rel(overlay, root)} {wrong}; nothing was removed")
    if overlay.is_symlink():
        raise SaveStateError(f"the write overlay {rel(overlay, root)} is a symlink; nothing is "
                             "removed through one")
    levels = [[overlay]]
    for part in SAVE_DIR:
        found = [m for d in levels[-1] for m in _named(d, part)]
        linked = [m for m in found if m.is_symlink()]
        if linked:
            raise SaveStateError(f"{rel(linked[0], root)} in the write overlay is a symlink; "
                                 "nothing is removed through one")
        levels.append(found)
    removed: dict = {}
    for save_dir in levels[-1]:
        removed.update(_contents(save_dir, overlay))
        _remove(save_dir)
    for level in reversed(levels[1:-1]):         # gd_PC, then resource
        for d in level:
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
                removed[rel(d, overlay)] = "<dir>"
    return {"pin": pin.record(root), "installation": rel(Path(install), root),
            "installation_matches_pin": True, "overlay": rel(overlay, root),
            "removed": removed}


def lifted_roots(cmd: list[str], root: Path = ROOT) -> tuple[Path, Path]:
    """The installation and the write overlay the run of a tools/s4/loop_run.py
    command reads: its --run's settings over loop_run's BASE_ENV, relative to
    `root`, where loop_run runs the guest. One --run, as the replay tools pass."""
    runs = [cmd[i + 1] for i, a in enumerate(cmd[:-1]) if a == "--run"]
    if len(runs) != 1 or any(a.startswith("--run=") for a in cmd):
        raise SaveStateError(f"a lifted run's command must carry one --run, and this has "
                             f"{len(runs)}: which overlay each run reads would be a guess")
    env = dict(LIFTED_DEFAULTS)
    for kv in runs[0].split("|")[1:]:
        k, _, v = kv.partition("=")
        if k in env:
            env[k] = v
    install, overlay = (Path(env[k]) for k in ("SA2_INSTALL", "SA2_VFS_WRITE_ROOT"))
    return (install if install.is_absolute() else root / install,
            overlay if overlay.is_absolute() else root / overlay)


def left_in(overlay: Path) -> dict:
    """Every entry `overlay` holds, as path relative to it -> sha256 of a
    regular file, "<dir>", "<symlink>" or "<other>"; {} when it holds nothing
    or does not exist yet."""
    overlay = Path(overlay)
    if overlay.is_symlink() or not overlay.is_dir():
        return {}
    out: dict = {}
    for entry in sorted(overlay.iterdir()):
        out.update(_contents(entry, overlay))
    return out


def reset_lifted(cmd: list[str], root: Path = ROOT) -> dict:
    """reset_overlay for the run a loop_run command starts, under `root`: the
    pin in root's Documentation/save-state.json, and the installation and
    overlay the command's run reads (lifted_roots). The record also keeps what
    the overlay still holds after the reset (`left`): the guest reads the
    overlay first and lists it when it searches a directory the overlay holds,
    and C:\\SA2 is always one, so anything left there, not only a save
    directory, is part of the run's input (P6's review, W3)."""
    install, overlay = lifted_roots(cmd, root)
    record = reset_overlay(overlay, install, load(root / PIN.relative_to(ROOT)), root)
    record["left"] = left_in(overlay)
    return record


def require_empty(record: dict) -> None:
    """SaveStateError naming what a reset overlay still holds, unless it holds
    nothing (reset_lifted's `left`). For the two runs that are compared field
    for field, the reach run and the boot replay's hooked run
    (tools/s4/tick_replay.py non_perturbation): both must start from the same
    overlay, and an empty one is the only state each can reproduce alone."""
    left = record.get("left")
    if left is None:
        raise SaveStateError("the reset recorded nothing about what the write overlay holds "
                             "after it (`left`), so nothing shows the run started from an "
                             "empty one")
    if left:
        shown = ", ".join(sorted(left)[:5]) + (f" and {len(left) - 5} more" if len(left) > 5
                                                else "")
        raise SaveStateError(f"the write overlay {record.get('overlay')} still holds {shown} "
                             "after its saves were cleared; the guest reads and lists the "
                             "overlay first, so this run would not be the one its record "
                             "describes. Remove them (tools/s4/vfs_overlay.py --clear empties "
                             "the overlay), or give the run an empty overlay of its own "
                             "(SA2_VFS_WRITE_ROOT)")


def native_start_problems(capture: Path, root: Path = ROOT) -> list[str]:
    """Why the native capture a lifted replay stands for did not start from
    the saves that replay starts from, or [] when it did: its receipt's
    session.saves.at_start (tools/oracle/run_oracle_probe.py) against the pin
    in root's Documentation/save-state.json, which reset_lifted gives the
    lifted run. Without it a lifted replay and its native capture could start
    from two states and nothing would say so (the save-state review of
    2026-09-24, N5). A capture with no receipt is left to the caller's own
    check, which names it; a pin that cannot be read is a reason."""
    receipt = Path(capture) / "receipt.json"
    if not receipt.is_file():
        return []
    where = rel(Path(capture), root)
    try:
        pin = load(root / PIN.relative_to(ROOT))
    except SaveStateError as e:
        return [str(e.code)]
    try:
        saves = (json.loads(receipt.read_text()).get("session") or {}).get("saves") or {}
    except (OSError, ValueError) as e:
        return [f"{where}'s receipt cannot be read: {e}"]
    began = saves.get("at_start") if isinstance(saves, dict) else None
    if not isinstance(began, dict):
        return [f"{where}'s receipt records no start saves (session.saves.at_start), so nothing "
                f"shows it started from the pinned saves a lifted replay of it starts from "
                f"({rel(pin.path, root)}, {pin.state}); re-capture it"]
    if digest(began) != pin.digest:
        return [f"{where} started from saves {digest(began)[:12]}, and a lifted replay of it "
                f"starts from the pin {pin.digest[:12]} ({rel(pin.path, root)}, {pin.state})"]
    return []


# ------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=("check", "restore", "digest", "reset-overlay"))
    ap.add_argument("--install", type=Path, default=INSTALL,
                    help=f"the installation (default {rel(INSTALL)})")
    ap.add_argument("--overlay", type=Path, default=OVERLAY,
                    help=f"reset-overlay: the lifted write overlay (default {rel(OVERLAY)})")
    ap.add_argument("--pin", type=Path, default=None, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    try:
        pin = load(a.pin)
        if a.action == "digest":
            have = state(a.install)
            print(f"SAVE_STATE_PIN {pin.digest} {pin.state}")
            print(f"SAVE_STATE_INSTALL {digest(have)} "
                  f"{'matches' if not problems(a.install, pin) else 'differs from'} the pin")
            return 0
        if a.action == "check":
            wrong = problems(a.install, pin)
            if wrong:
                print("SAVE_STATE_DIFFERS " + refusal(a.install, pin, wrong))
                return 1
            print(f"SAVE_STATE_OK {pin.digest} {rel(a.install)}/{'/'.join(SAVE_DIR)} is the pin "
                  f"({pin.state})")
            return 0
        if a.action == "restore":
            done = restore(a.install, pin)
            for line in done:
                print(f"  {line}")
            print(f"SAVE_STATE_OK {pin.digest} "
                  f"{'restored' if done else 'already the pin'}: {rel(a.install)}")
            return 0
        record = reset_overlay(a.overlay, a.install, pin)
        print(json.dumps(record, indent=1))
        print(f"SAVE_STATE_OVERLAY_RESET removed {len(record['removed'])} entries from "
              f"{record['overlay']}")
        return 0
    except SaveStateError as e:
        print(f"SAVE_STATE_REFUSED {e.code}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
