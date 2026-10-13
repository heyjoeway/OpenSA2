#!/usr/bin/env python3
"""Write the installer receipt from the fresh-host route runs' records.

Subtitle: `.venv/bin/python tools/s7/installer_receipt.py --runs DIR [--runs DIR ...]` reads each
DIR that `host/launch/fresh_host_routes.py --out DIR` (x86-64 Linux) or
`host/launch/windows_routes.py --out DIR` (x86-64 Windows, under Wine or natively) wrote (its
`fresh-routes.runrec`, every record that names, the session folders beside them and the release's
manifest copied there),
together with every run folder the receipt already on disk cites, recomputes each route's verdict
from that evidence, and writes `Documentation/receipts/s7-installer-v1.json` to the contract of
`Documentation/scripts/check_s7_exit.py` `installer`. `--check` recomputes from the run folders
the receipt cites (and any --runs) and compares, writing nothing.

It is a measuring instrument: G7's installer clause reads what it writes. So:

* **Nothing is carried on trust.** The receipt is a function of the run folders it cites
  (`inputs.runs`) and the tree: a rebuild re-reads every cited folder (refusing when one is gone,
  unless `--reset`), and an entry no run read now explains is not written. A hand-typed entry, or
  a first pass moved earlier, therefore makes `--check` read stale.
* **No flag a record states about itself is taken where the evidence is on disk.** The launch
  clause re-reads the game's run record, the observer's table and the player's report from the
  session folder (each held to the hash the launch record gives it), and the identities of the
  binaries from the release manifest the run folder holds (held to the hash its summary gives it).
* A record whose bytes are not the ones its summary hashed is refused.

What a route's run on a target must show, each clause from its own evidence
--------------------------------------------------------------------------
The summary (`fresh-routes.runrec`, schema `sa2-s7-fresh-routes/v1`): the host was fresh, by its
runner's rule (`RUNNERS`), the steps were exactly `install` then `launch` with the exits expected,
and a write into the source failed as a read-only file system where the runner can make it one.
A run that fails any of these is evidence of no clause. The runners:

* `bwrap` (x86-64 Linux, fresh_host_routes.py; a summary that names no runner is one): the sandbox
  saw none of the checkout, the private/ tree, a Proton prefix or the Steam folders it must not
  see, HOME was an empty tmpfs, and the write probe was refused.
* `wine` (x86-64 Windows under Proton's Wine, windows_routes.py): the same sandbox around a new
  WINEPREFIX with no Z: drive, the release at `C:/Program Files/sa2recomp` and the records at O:.
* `native` (x86-64 Windows, the user's own run, windows_routes.py --runner native): no sandbox;
  each route with a new, empty LOCALAPPDATA; the zip route's archive read only (refused), the
  Steam folder the user's own and not probed. The install record's manifests before and after
  (`installed`) are then the evidence that the source was left alone.

The receipt keeps each Windows run's `runner`, so a Wine run never reads as the user's native one.

* **hash_checked**: the install record (schema `sa2-s7-install-run/v1`) says `hash_checked`, in
  the `full` mode, and its counts agree: every table entry ok or substituted, none missing, none
  changed, no extra refused, the entries the table's; its table is the tracked install-hashes
  receipt's (`id`, and the receipt file's sha256) with the executable checked per section under
  the user's DRM ruling; for the zip route, every file the archive holds was read and checked.
* **installed**: the install exited `accepted` (0) and says `installed`; the source's manifests
  before and after the run are equal and not empty; for the zip route the archive it read is the
  one the summary made from the Steam install.
* **manual_steps**: counted from the summary's steps, not the installer's own constant: the steps
  are the route's two commands exactly (the install with the route's source and `--yes`, the
  steam route with no path, the folder and zip routes with the path the sandbox binds; then
  `sa2recomp --selftest --frames N` with N at least MIN_FRAMES and nothing else), so nothing was
  moved, renamed, edited or flagged by hand; 0 then, and no count otherwise.
* **launched**: the launch record (schema `sa2-s7-launch-run/v1`) is a selftest's that says
  `launched` with no failure, and the evidence agrees:
  - the game's run record: ok; the guest ended itself (ExitProcess); the closed world in the trap
    mode of an automated session with no method off the M7 list; the loader mapped the shipped
    executable through the overlay; its file system ready, writing only the session's overlay;
    its file logs whole (each count equal to its list's length); no table path not found, no
    file opened that is neither a table file nor a user file, no path refused;
  - the observer's table: the title (GameMode 14, CurrentMenu 0) held for the frames asked, then
    the main menu (CurrentMenu 1) after the Enters, each Enter read by the game;
  - the relay handed on every byte and frame, and the player presented every frame, exited 0 and
    wrote a final report;
  - the installation verified at launch; the launcher, installer, game and player are the files
    the release manifest lists, the manifest the summary names; the table is the install's;
  - the release's sources (the manifest's `sources`) are the tree's, for the last run of a route
    (a run of older sources still counts for the first pass, the historical fact).
* **first_passed**: the `finished` of the earliest run that passes every clause, with its evidence
  (`first_pass_evidence`). Earlier runs stay counted as long as the receipt cites their folders;
  `--reset` starts the order again from the runs given (for instance after the DRM measurement is
  rewritten, when no earlier pass can count).

The `x86_64-windows` entries are written from the Windows runs' records (windows_routes.py): the
Wine runs' first, and the user's native run's when it is given; each keeps its runner. `drm_unwrap` cites the measurement's receipt and, since its bytes differ,
the user's ruling (`Documentation/decisions/2026-10-11-S7-drm-unwrap-ruling.md`).

Usage:
  .venv/bin/python tools/s7/installer_receipt.py --runs DIR [--runs DIR ...] [--out R] [--reset]
  .venv/bin/python tools/s7/installer_receipt.py --check [--runs DIR ...]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = "tools/s7/installer_receipt.py"
RECEIPT = "Documentation/receipts/s7-installer-v1.json"
SCHEMA = "sa2-s7-installer/v1"
ROUTES = ("steam-autodiscovery", "folder", "zip")
TARGETS = ("x86_64-linux", "x86_64-windows")
SUMMARY = "fresh-routes.runrec"
SUMMARY_SCHEMA = "sa2-s7-fresh-routes/v1"
INSTALL_SCHEMA = "sa2-s7-install-run/v1"
LAUNCH_SCHEMA = "sa2-s7-launch-run/v1"
MANIFEST = "release.manifest"
STEPS = ("install", "launch")
SOURCE_KIND = {"steam-autodiscovery": "steam", "folder": "folder", "zip": "zip"}
HASHES = "Documentation/receipts/install-hashes-v1.json"
DRM_RECEIPT = "Documentation/receipts/s7-drm-unwrap-v1.json"
RULING = "Documentation/decisions/2026-10-11-S7-drm-unwrap-ruling.md"
# The sandbox's fixed places (host/launch/fresh_host_routes.py RELEASE_IN, OUT_IN).
RELEASE_IN = "/opt/sa2recomp"
OUT_IN = "/out"
# How each target's runs were made, and what each runner's fresh host is (module docstring): its
# HOME, the write probe each route must show, and the places it must give the release and the
# records (None: any absolute Windows path, as the summary states it).
NATIVE_HOME = "a new, empty LOCALAPPDATA"
NATIVE_UNPROBED = "not-probed: a native host's Steam folder is the user's"
RUNNERS = {
    "bwrap": {"targets": ("x86_64-linux",), "home": "an empty tmpfs", "release_at": RELEASE_IN,
              "out_at": OUT_IN, "probe": {r: "refused" for r in ("steam-autodiscovery", "folder", "zip")}},
    "wine": {"targets": ("x86_64-windows",), "home": "an empty tmpfs",
             "release_at": "C:/Program Files/sa2recomp", "out_at": "O:",
             "probe": {r: "refused" for r in ("steam-autodiscovery", "folder", "zip")}},
    "native": {"targets": ("x86_64-windows",), "home": NATIVE_HOME, "release_at": None, "out_at": None,
               "probe": {"steam-autodiscovery": NATIVE_UNPROBED, "folder": NATIVE_UNPROBED, "zip": "refused"}},
}
EXE_SUFFIX = {"x86_64-linux": "", "x86_64-windows": ".exe"}
WINDOWS_PATH = re.compile(r"^[A-Za-z]:(/[^/]+)*$")
# The least title the selftest must hold, in presented frames: the launcher's default.
MIN_FRAMES = 300
# The observer's state words' values (Documentation/scripts/check_s5_exit.py STATE_WORDS): the
# title is GameMode_Advertise with Menus_TitleScreen, the main menu Menus_Main.
GAME_MODE_ADVERTISE, MENU_TITLE, MENU_MAIN = 14, 0, 1
DIK_RETURN = 0x1C
# The binaries the launch record names, by its `tool` key and the manifest's path.
BINARIES = {"sha256": "bin/sa2recomp", "installer_sha256": "bin/sa2-install",
            "game_sha256": "bin/sa2-game"}
PLAYERS = {"sdl3-gpu": "bin/sa2_player_gpu", "dxvk-native": "bin/sa2_player"}
# The tools whose code made the records and this receipt, pinned in inputs.pipeline
# (Documentation/scripts/check_receipt_pins.py holds each to the tree the receipt is written in),
# and the launcher's and installer's sources, whose binaries computed what the records say.
PIPELINE = (TOOL, "host/launch/fresh_host_routes.py", "host/launch/windows_routes.py",
            "tools/s7/release_build.py", "tools/s7/install_table.py")
PIPELINE_GLOBS = ("host/launch/*.cpp", "host/launch/*.h", "host/install/*.cpp", "host/install/*.h")
VERDICT = ("hash_checked", "installed", "manual_steps", "launched")
IGNORED_IN_CHECK = ("written", "command")
INSTALLER_DRM = "drm_unwrap"
HEX64 = re.compile(r"[0-9a-f]{64}")


class Refused(Exception):
    """An input that is not to its contract: nothing is written."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load(path: Path, what: str) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise Refused(f"{what}: {path} is absent") from None
    except ValueError as error:
        raise Refused(f"{what}: {path} is not JSON: {error}") from None
    if not isinstance(doc, dict):
        raise Refused(f"{what}: {path} is not a JSON object")
    return doc


def instant(value: object, what: str) -> datetime:
    if not isinstance(value, str):
        raise Refused(f"{what} is {value!r}, not an instant")
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise Refused(f"{what} is {value!r}, not an instant") from None
    if moment.tzinfo is None:
        raise Refused(f"{what} is {value!r}, with no UTC offset")
    return moment


def rel(path: Path, root: Path) -> str:
    path = Path(path).resolve()
    for base, shown in ((root.resolve(), ""), (Path.home().resolve(), "~/")):
        try:
            return shown + str(path.relative_to(base))
        except ValueError:
            continue
    return str(path)


def unrel(shown: str, root: Path) -> Path:
    """The path `rel` wrote as `shown`."""
    if shown.startswith("~/"):
        return Path.home() / shown[2:]
    p = Path(shown)
    return p if p.is_absolute() else root / p


def dig(doc: object, *keys: str) -> object:
    for k in keys:
        doc = doc.get(k) if isinstance(doc, dict) else None
    return doc


def is_sha(value: object) -> bool:
    return isinstance(value, str) and HEX64.fullmatch(value) is not None


def fold(path: str) -> str:
    return path.replace("\\", "/").lower()


def user_file(folded: str) -> bool:
    """The launcher's and installer's rule (host/launch/sa2_launch.cpp user_file): a top-level
    Config/*.cfg or Config/*.ini, or a save under resource/gd_PC/SAVEDATA/."""
    if folded.startswith("config/") and "/" not in folded[7:] and folded.endswith((".cfg", ".ini")):
        return True
    return folded.startswith("resource/gd_pc/savedata/")


def pipeline_files(root: Path) -> list[str]:
    files = list(PIPELINE)
    for pattern in PIPELINE_GLOBS:
        files += sorted(p.relative_to(root).as_posix() for p in root.glob(pattern) if p.is_file())
    return files


def table_identity(root: Path) -> dict:
    """The tracked install-hashes receipt the installer's table must be generated from."""
    doc = load(root / HASHES, "the install-hashes receipt")
    files = doc.get("files") or []
    return {"id": doc.get("id"), "receipt_sha256": sha256_file(root / HASHES),
            "entries": len(files),
            "paths": frozenset(fold(str(f.get("path"))) for f in files if isinstance(f, dict))}


def drm_cited(root: Path) -> dict:
    doc = load(root / DRM_RECEIPT, "the DRM-unwrap measurement")
    if not isinstance(doc.get("bytes_match"), bool):
        raise Refused(f"{DRM_RECEIPT} states no boolean bytes_match")
    cited = {"receipt": DRM_RECEIPT, "receipt_sha256": sha256_file(root / DRM_RECEIPT),
             "bytes_match": doc["bytes_match"], "measured": doc.get("written")}
    if not doc["bytes_match"]:
        if not (root / RULING).is_file():
            raise Refused(f"the bytes differ and the ruling {RULING} is absent")
        cited["ruling"] = RULING
        cited["ruling_sha256"] = sha256_file(root / RULING)
    return cited


class Layout:
    """Where one run's programs saw the release and the records, and its binaries' suffix."""

    def __init__(self, target: str, runner: str, release_at: str, out_at: str):
        self.target, self.runner = target, runner
        self.release_at, self.out_at = release_at, out_at
        self.exe = EXE_SUFFIX[target]
        self.windows = target == "x86_64-windows"


def layout(summary: dict, rel_summary: str) -> Layout:
    """The summary's target, runner and places, refused when the runner is not one of the target's
    or the places are not the runner's."""
    target = summary.get("target")
    runner = dig(summary, "runner", "kind") or "bwrap"
    rule = RUNNERS.get(str(runner))
    if rule is None or target not in rule["targets"]:
        raise Refused(f"{rel_summary} ran on {target!r} by a {runner!r} runner, not one of "
                      f"{[k for k, v in RUNNERS.items() if target in v['targets']]}")
    places = {}
    for key in ("release_at", "out_at"):
        got = summary.get(key, rule[key] if runner == "bwrap" else None)
        want = rule[key]
        if want is not None and got != want:
            raise Refused(f"{rel_summary}: a {runner} run's {key} is {got!r}, not {want!r}")
        if want is None and not (isinstance(got, str) and WINDOWS_PATH.match(got)):
            raise Refused(f"{rel_summary}: a {runner} run's {key} {got!r} is not an absolute Windows path")
        places[key] = got
    return Layout(target, runner, places["release_at"], places["out_at"])


def same_path(a: object, b: object, lay: "Layout") -> bool:
    """Two paths as one host names them: on Windows case-blind, either separator."""
    if lay.windows:
        return isinstance(a, str) and isinstance(b, str) and fold(a).rstrip("/") == fold(b).rstrip("/")
    return a == b


def manifest(run_dir: Path, summary: dict) -> tuple[dict, dict, list[str]]:
    """(the release manifest's head, {path: sha256} of its files, problems), from the copy in
    `run_dir`, held to the hash the summary gives the release's manifest."""
    path = run_dir / MANIFEST
    want = dig(summary, "release", "manifest_sha256")
    if not path.is_file():
        return {}, {}, [f"the run folder holds no copy of the release's {MANIFEST}"]
    if sha256_file(path) != want:
        return {}, {}, [f"the run folder's {MANIFEST} is not the one the summary names ({want!r})"]
    head, files = {}, {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            try:
                head = json.loads(line[2:]) if not head else head
            except ValueError:
                pass
            continue
        parts = line.split("  ")
        if len(parts) == 3 and is_sha(parts[0]):
            files[parts[2]] = parts[0]
    return head, files, []


# ---------------------------------------------------------------- one route's run

def expected_steps(route: str, entry: dict, lay: "Layout | None" = None) -> list[str]:
    """The problems with the route's steps as the summary records them: exactly the route's two
    commands, nothing added (manual_steps). A folder or archive given to a sandboxed install is
    one the sandbox binds read only (by its path there, or by the Windows path Wine maps it to);
    a native run has no binds, and its source is any path of the right shape."""
    lay = lay or Layout("x86_64-linux", "bwrap", RELEASE_IN, OUT_IN)
    problems = []
    steps = [s for s in entry.get("steps") or [] if isinstance(s, dict)]
    if tuple(s.get("name") for s in steps) != STEPS:
        return [f"the route's steps are {[s.get('name') for s in steps]}, not {list(STEPS)}"]
    bound = {str(b.get(k)) for b in entry.get("binds_read_only") or [] if isinstance(b, dict)
             for k in ("at", "windows") if b.get(k)}
    inst, launch = (s.get("argv") if isinstance(s.get("argv"), list) else [] for s in steps)
    out = lay.out_at
    record = [f"--record", f"{out}/{route}-install.runrec"]
    head = [f"{lay.release_at}/bin/sa2-install{lay.exe}", "--source", SOURCE_KIND[route]]
    if route == "steam-autodiscovery":
        ok = inst == head + ["--yes"] + record
    else:
        ok = (len(inst) == 7 and inst[:3] == head and inst[4:] == ["--yes"] + record
              and (inst[3] in bound or lay.runner == "native")
              and (route != "folder" or inst[3].endswith("/steamapps/common/Sonic Adventure 2"))
              and (route != "zip" or inst[3].endswith(".zip")))
    if not ok:
        problems.append(f"the install step ran {inst!r}, not the {route} route's command")
    frames = launch[3] if len(launch) == 8 else None
    if not (len(launch) == 8 and launch[:3] == [f"{lay.release_at}/bin/sa2recomp{lay.exe}", "--selftest", "--frames"]
            and str(frames).isdigit() and int(str(frames)) >= MIN_FRAMES
            and launch[4:] == ["--record", f"{out}/{route}-launch.runrec", "--out", f"{out}/{route}-session"]):
        problems.append(f"the launch step ran {launch!r}, not `sa2recomp --selftest --frames N` with N at "
                        f"least {MIN_FRAMES} and nothing else")
    return problems


def check_install(rec: dict, route: str, target: str, table: dict,
                  archive_sha256: str | None = None) -> tuple[dict, dict]:
    """The install record's hash_checked and installed clauses recomputed, the problems that make
    each false, by clause, and what the receipt keeps of it."""
    why: dict[str, list[str]] = {"hash_checked": [], "installed": []}
    common = []
    if rec.get("schema") != INSTALL_SCHEMA:
        common.append(f"the install record's schema is {rec.get('schema')!r}")
    if rec.get("target") != target:
        common.append(f"the install ran on {rec.get('target')!r}, not {target}")
    if rec.get("route") != route or dig(rec, "source", "kind") != SOURCE_KIND[route]:
        common.append(f"the install record is the {rec.get('route')!r} route's, from a "
                      f"{dig(rec, 'source', 'kind')!r} source")
    if not is_sha(dig(rec, "tool", "sha256")):
        common.append(f"the install record names its installer as {dig(rec, 'tool', 'sha256')!r}, "
                      "not a sha256")
    for clause in why:
        why[clause] += common
    h = why["hash_checked"]
    if rec.get("hash_checked") is not True:
        h.append(f"the install record says hash_checked {rec.get('hash_checked')!r}")
    if rec.get("verified_mode") != "full":
        h.append(f"the install verified in mode {rec.get('verified_mode')!r}, not 'full'")
    t = rec.get("table") if isinstance(rec.get("table"), dict) else {}
    if (t.get("id"), t.get("receipt_sha256"), t.get("entries")) != \
            (table["id"], table["receipt_sha256"], table["entries"]):
        h.append(f"the install's table is {t.get('id')!r} {t.get('receipt_sha256')!r} of "
                 f"{t.get('entries')!r} entries, not {HASHES}'s {table['id']!r} "
                 f"{table['receipt_sha256']} of {table['entries']}")
    exe = t.get("exe_check") if isinstance(t.get("exe_check"), dict) else {}
    if exe.get("kind") != "per-section" or exe.get("ruling") != RULING:
        h.append(f"the executable was checked {exe.get('kind')!r} under {exe.get('ruling')!r}, "
                 f"not per section under {RULING}")
    c = rec.get("counts") if isinstance(rec.get("counts"), dict) else {}
    n = {k: c.get(k) for k in ("entries", "ok", "substituted", "missing", "modified", "extras_refused")}
    if not (all(type(v) is int for v in n.values()) and n["entries"] == table["entries"]
            and n["missing"] == 0 and n["modified"] == 0 and n["extras_refused"] == 0
            and n["ok"] + n["substituted"] == n["entries"]):
        h.append(f"the install's counts {n} are not every table entry verified")
    if route == "zip" and dig(rec, "zip", "files_verified") is not True:
        h.append("the archive's files were not all read and checked (zip.files_verified)")
    i = why["installed"]
    code = dig(rec, "exit", "code")
    if code != 0 or rec.get("installed") is not True:
        i.append(f"the install exited {code!r} ({dig(rec, 'exit', 'name')!r}), installed "
                 f"{rec.get('installed')!r}")
    before, after = dig(rec, "source_manifest", "before"), dig(rec, "source_manifest", "after")
    if not isinstance(before, str) or not before or before != after:
        i.append("the source the install read (the folder, or the archive) was changed during "
                 "the run: its manifests before and after differ")
    if route == "zip" and (not archive_sha256 or f"sha256={archive_sha256} " not in str(before)):
        i.append(f"the archive installed is not the one the summary names ({archive_sha256!r})")
    if rec.get("manual_steps") != 0 or type(rec.get("manual_steps")) is not int:
        i.append(f"the install record counts manual steps as {rec.get('manual_steps')!r}")
    kept = {"record_schema": rec.get("schema"), "tool_sha256": dig(rec, "tool", "sha256"),
            "source": {"kind": dig(rec, "source", "kind"), "path": dig(rec, "source", "path"),
                       "read_root": dig(rec, "source", "read_root")},
            "counts": c, "exe_check": exe, "verified_mode": rec.get("verified_mode"),
            "source_manifest_equal": isinstance(before, str) and bool(before) and before == after,
            "extras_kept_under_resource": sorted(
                str(e.get("path")) for e in rec.get("extras") or []
                if isinstance(e, dict) and str(e.get("path", "")).startswith("resource/")
                and not e.get("refused")),
            "seconds": rec.get("seconds")}
    if isinstance(rec.get("user_defaults"), list):
        # The game's own Config/ files (UserConfig.cfg, Keyboard.cfg): whether the source held
        # each, and whether the installer wrote its default into the overlay
        # (host/install/sa2_config.h, sa2_user_defaults_ensure).
        kept["user_defaults"] = rec["user_defaults"]
    if route == "zip":
        z = rec.get("zip") if isinstance(rec.get("zip"), dict) else {}
        kept["zip"] = {k: z.get(k) for k in ("root", "archive_bytes", "entries", "methods",
                                             "files_verified", "extracted", "user_files_written",
                                             "extracted_extras")}
        kept["store"] = rec.get("store")
    return {"verdict": {"hash_checked": not h, "installed": not i}, "kept": kept}, why


def session_file(run_dir: Path, shown: object, want: object, what: str,
                 out_at: str = OUT_IN) -> tuple[object, list[str]]:
    """A file the launch record names at its path under the records' folder as the run saw it
    (`out_at`: /out in a sandbox, O: under Wine), read from `run_dir` and held to the hash the
    record gives it: (its parsed JSON, or its text for a .tsv; problems)."""
    s = str(shown)
    if not s.startswith(out_at + "/"):
        return None, [f"the launch record names the {what} at {s!r}, not under {out_at}"]
    path = run_dir / s[len(out_at) + 1:]
    if not path.is_file():
        return None, [f"the {what} {rel(path, run_dir)} is not in the run folder"]
    if sha256_file(path) != want:
        return None, [f"the {what} is not the file the launch record hashed"]
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".tsv":
        return text, []
    try:
        return json.loads(text), []
    except ValueError:
        return None, [f"the {what} is not JSON"]


def check_game(doc: dict, table: dict, write_root: str, config_read_root: str,
               lay: "Layout | None" = None) -> list[str]:
    """The game's own run record (harness/crt_probe.c's), every clause recomputed."""
    p = []
    if doc.get("ok") is not True:
        p.append("the game's run record says ok false")
    if "ExitProcess" not in str(dig(doc, "trap", "note")):
        p.append(f"the guest did not end itself: {dig(doc, 'trap', 'note')!r}")
    cw = dig(doc, "d3d9", "closed_world") or {}
    if dig(cw, "mode") != "trap":
        p.append(f"the closed world ran in mode {dig(cw, 'mode')!r}, not 'trap'")
    if dig(cw, "automated_session") is not True:
        p.append("the device did not run as an automated session")
    if dig(cw, "unlisted_methods") != 0 or dig(cw, "unlisted_calls") != 0:
        p.append(f"the closed world counted {dig(cw, 'unlisted_methods')!r} methods "
                 f"({dig(cw, 'unlisted_calls')!r} calls) off the M7 list")
    if dig(doc, "exe", "kind") != "shipped" or dig(doc, "exe", "overlay", "loaded") is not True:
        p.append(f"the loader mapped the {dig(doc, 'exe', 'kind')!r} executable, not the shipped one "
                 "through the overlay")
    files = doc.get("files") if isinstance(doc.get("files"), dict) else {}
    lay = lay or Layout("x86_64-linux", "bwrap", RELEASE_IN, OUT_IN)
    read_tail = config_read_root.lstrip("~")
    read_ok = (fold(str(files.get("read_root"))).endswith(fold(read_tail)) if lay.windows
               else str(files.get("read_root")).endswith(read_tail))
    if files.get("ready") is not True or not same_path(files.get("write_root"), write_root, lay) or not read_ok:
        p.append(f"the game's file system read {files.get('read_root')!r} and wrote "
                 f"{files.get('write_root')!r}, not the installation's folder and the session's overlay")
    res = files.get("resolutions") if isinstance(files.get("resolutions"), list) else []
    opened = files.get("opened") if isinstance(files.get("opened"), list) else []
    stats = files.get("stats") if isinstance(files.get("stats"), dict) else {}
    if stats.get("resolves") != len(res) or stats.get("opens") != len(opened) or not res:
        p.append(f"the file log was truncated: {len(res)} of {stats.get('resolves')!r} resolutions and "
                 f"{len(opened)} of {stats.get('opens')!r} opens logged")
    not_found = sorted({str(r.get("rel")) for r in res if isinstance(r, dict)
                        and r.get("status") == "not-found" and fold(str(r.get("rel"))) in table["paths"]})
    if not_found:
        p.append(f"table paths not found: {not_found}")
    refused = sorted({f"{r.get('rel')} ({r.get('status')})" for r in res if isinstance(r, dict)
                      and r.get("status") not in ("ok", "not-found")})
    if refused:
        p.append(f"paths refused: {refused}")
    stray = sorted({str(o.get("rel")) for o in opened if isinstance(o, dict)
                    and fold(str(o.get("rel"))) not in table["paths"]
                    and not user_file(fold(str(o.get("rel"))))})
    if stray:
        p.append(f"installation files opened that the table does not hold: {stray}")
    return p


def check_ticks(text: str, frames: int) -> tuple[list[str], dict]:
    """The observer's table (release_build.py observer_source): the title held for `frames`
    presents, then the main menu; each K row a poll that read the launcher's key records."""
    rows, polls = [], []
    for line in text.splitlines():
        f = line.split("\t")
        if f[0] == "T" and len(f) >= 5 and all(x.isdigit() for x in f[1:5]):
            rows.append(tuple(int(x) for x in f[1:5]))
        elif f[0] == "K" and len(f) >= 6 and all(x.isdigit() for x in f[1:6]):
            polls.append(tuple(int(x) for x in f[1:6]))
    start, held, main = None, None, None
    for tick, presents, mode, menu in rows:
        if held is None:
            if mode == GAME_MODE_ADVERTISE and menu == MENU_TITLE:
                start = start if start is not None else (tick, presents)
                if presents - start[1] >= frames:
                    held = (tick, presents)
            else:
                start = None
        elif mode == GAME_MODE_ADVERTISE and menu == MENU_MAIN:
            main = (tick, presents)
            break
    problems = []
    if held is None:
        problems.append(f"the observer's table never holds the title for {frames} frames")
    elif main is None:
        problems.append("the observer's table never reaches the main menu after the title")
    elif not any(held[0] <= k[0] <= main[0] for k in polls):
        problems.append("the observer's table shows no key the game read between the title and the main menu")
    return problems, {"title_first_tick": start[0] if start else None,
                      "title_held_tick": held[0] if held else None,
                      "main_menu_tick": main[0] if main else None, "key_polls": len(polls)}


def check_launch(rec: dict, run_dir: Path, route: str, target: str, install: dict, summary: dict,
                 table: dict, release: tuple[dict, dict, list[str]],
                 lay: "Layout | None" = None) -> tuple[list[str], dict]:
    """The problems that make `launched` false, recomputed from the launch record and the files
    of its session, and what the receipt keeps of it."""
    problems: list[str] = list(release[2])
    lay = lay or Layout(target if target in EXE_SUFFIX else "x86_64-linux", "bwrap", RELEASE_IN, OUT_IN)
    if rec.get("schema") != LAUNCH_SCHEMA:
        problems.append(f"the launch record's schema is {rec.get('schema')!r}")
    if rec.get("mode") != "selftest":
        problems.append(f"the launch record is a {rec.get('mode')!r} session's, not a selftest's")
    if rec.get("target") != target:
        problems.append(f"the launch ran on {rec.get('target')!r}, not {target}")
    if rec.get("launched") is not True or rec.get("failures") != []:
        problems.append(f"the launch record says launched {rec.get('launched')!r}, failures "
                        f"{rec.get('failures')!r}")
    if dig(rec, "verify", "ran") is not True:
        problems.append("the installation was not verified at launch")
    if dig(rec, "verify", "exit") != 0:
        problems.append(f"the verify at launch exited {dig(rec, 'verify', 'exit')!r}")
    if (dig(rec, "table", "id"), dig(rec, "table", "receipt_sha256")) != (table["id"], table["receipt_sha256"]):
        problems.append("the launcher's table is not the install-hashes receipt's")
    if dig(rec, "config", "source_kind") != SOURCE_KIND[route]:
        problems.append(f"the launch read a {dig(rec, 'config', 'source_kind')!r} installation")
    # The binaries: each a sha256, each the manifest's file, the installer the one that installed.
    tool = rec.get("tool") if isinstance(rec.get("tool"), dict) else {}
    head, listed = release[0], release[1]
    names = {k: v + lay.exe for k, v in dict(BINARIES, player_sha256=PLAYERS.get(str(rec.get("host")),
                                                                          "bin/?")).items()}
    for key, path in names.items():
        if not is_sha(tool.get(key)):
            problems.append(f"the launch record names its {path} as {tool.get(key)!r}, not a sha256")
        elif listed and listed.get(path) != tool.get(key):
            problems.append(f"the {path} that ran is not the release manifest's")
    if tool.get("release_manifest_sha256") != dig(summary, "release", "manifest_sha256") or \
            not is_sha(tool.get("release_manifest_sha256")):
        problems.append("the launcher ran from another release than the summary names")
    if tool.get("installer_sha256") != install["kept"]["tool_sha256"]:
        problems.append("the launcher's installer is not the one that installed")
    # The selftest, from the observer's table.
    st = rec.get("selftest") if isinstance(rec.get("selftest"), dict) else {}
    frames = st.get("frames")
    if type(frames) is not int or frames < MIN_FRAMES:
        problems.append(f"the selftest held the title for {frames!r} frames, fewer than {MIN_FRAMES}")
        frames = MIN_FRAMES
    enters = st.get("enters") if isinstance(st.get("enters"), list) else []
    if not enters or not all(isinstance(e, dict) and e.get("read") is True and e.get("dik") == DIK_RETURN
                             and e.get("released") is True
                             and isinstance(e.get("read_tick"), int) and isinstance(e.get("tick"), int)
                             and e["read_tick"] >= e["tick"] for e in enters):
        problems.append("the selftest's Enters were not all read and released by the game")
    if st.get("write_root") != f"{lay.out_at}/{route}-session/overlay":
        problems.append(f"the selftest's game wrote to {st.get('write_root')!r}, not its session's own overlay")
    g = rec.get("game") if isinstance(rec.get("game"), dict) else {}
    ticks, why = session_file(run_dir, g.get("ticks"), g.get("ticks_sha256"), "observer's table", lay.out_at)
    problems += why
    seen = {}
    if isinstance(ticks, str):
        why, seen = check_ticks(ticks, frames)
        problems += why
    game, why = session_file(run_dir, g.get("record"), g.get("record_sha256"), "game's run record", lay.out_at)
    problems += why
    if isinstance(game, dict):
        problems += check_game(game, table, str(st.get("write_root")), str(dig(rec, "config", "read_root")), lay)
    # The relay and the player.
    r = rec.get("relay") if isinstance(rec.get("relay"), dict) else {}
    if r.get("framing_problem") != "" or r.get("bytes_dropped") != 0 or \
            r.get("bytes_in") != r.get("bytes_out") or r.get("frames_in") != r.get("frames_out") or \
            not isinstance(r.get("frames_out"), int) or r.get("frames_out", 0) < frames:
        problems.append(f"the relay did not hand on every byte and frame it took: {r!r}")
    p = rec.get("player") if isinstance(rec.get("player"), dict) else {}
    if p.get("exit") != 0 or p.get("cancelled") is not False:
        problems.append(f"the player exited {p.get('exit')!r}, cancelled {p.get('cancelled')!r}")
    report, why = session_file(run_dir, p.get("report"), p.get("report_sha256"), "player's report", lay.out_at)
    problems += why
    if isinstance(report, dict):
        if report.get("complete") is not True or report.get("exit_status") != 0:
            problems.append(f"the player's report is complete {report.get('complete')!r} with exit "
                            f"status {report.get('exit_status')!r}")
        if not isinstance(report.get("presents"), int) or report["presents"] < (r.get("frames_out") or 0):
            problems.append(f"the player presented {report.get('presents')!r} of the "
                            f"{r.get('frames_out')!r} frames the relay handed it")
    kept = {"record_schema": rec.get("schema"), "host": rec.get("host"),
            "launcher_sha256": tool.get("sha256"), "installer_sha256": tool.get("installer_sha256"),
            "game_sha256": tool.get("game_sha256"), "player_sha256": tool.get("player_sha256"),
            "release_manifest_sha256": tool.get("release_manifest_sha256"),
            "frames": st.get("frames"), "observed": seen,
            "enters": [{k: e.get(k) for k in ("tick", "current_menu", "read_tick")}
                       for e in enters if isinstance(e, dict)],
            "write_root": st.get("write_root"), "read_root_saves": st.get("read_root_saves"),
            "exe": g.get("exe"), "opened": g.get("opened"),
            "opened_not_in_table": g.get("opened_not_in_table"),
            "table_paths_not_found": g.get("table_paths_not_found"),
            "relay": {k: r.get(k) for k in ("bytes_in", "frames_in", "frames_out", "bytes_dropped")},
            "player": {"video_driver": p.get("video_driver"), "window": p.get("window"),
                       "d3d9gpu_present": p.get("d3d9gpu_present"), "presents": p.get("presents")},
            "ended_by": rec.get("ended_by"), "seconds": rec.get("seconds")}
    return problems, kept


def sources_differ(head: dict, root: Path) -> list[str]:
    """The release manifest's sources that are not the tree's (absent, or other bytes)."""
    src = head.get("sources")
    if not isinstance(src, dict) or not src:
        return ["the release manifest names no sources"]
    return sorted(p for p, h in src.items()
                  if not (root / p).is_file() or sha256_file(root / p) != h)


def route_run(run_dir: Path, summary: dict, entry: dict, target: str, table: dict,
              root: Path, lay: "Layout | None" = None) -> dict:
    """One route's run on `target`, from its summary entry and the evidence it names. A problem
    of the run as a whole (the fresh host, the steps, the write probe) falsifies every clause:
    such a run is evidence of none of them."""
    route = entry.get("route")
    lay = lay or Layout(target if target in EXE_SUFFIX else "x86_64-linux", "bwrap", RELEASE_IN, OUT_IN)
    rule = RUNNERS[lay.runner]
    whole: list[str] = []
    if entry.get("sandbox_clean") is not True or entry.get("home") != rule["home"]:
        whole.append("the sandbox was not a clean fresh host" if lay.runner == "bwrap" else
                     f"the host was not a fresh one by the {lay.runner} runner's rule")
    if entry.get("write_probe") != rule["probe"].get(str(route)):
        whole.append(f"the write probe into the source reads {entry.get('write_probe')!r}")
    steps = [s for s in entry.get("steps") or [] if isinstance(s, dict)]
    if tuple(s.get("name") for s in steps) != STEPS:
        whole.append(f"the route's steps are {[s.get('name') for s in steps]}, not {list(STEPS)}")
    records = {}
    for s in steps:
        if s.get("name") not in STEPS:
            continue
        path = run_dir / str(s.get("record"))
        if not path.is_file():
            raise Refused(f"{rel(path, root)} is absent")
        if sha256_file(path) != s.get("record_sha256"):
            raise Refused(f"{rel(path, root)} is not the record {SUMMARY} hashed")
        if s.get("expected_exit") != 0 or s.get("exit") != 0:
            whole.append(f"the {s['name']} step exited {s.get('exit')!r}")
        records[s["name"]] = (path, load(path, f"the {route} route's {s['name']} record"))
    if set(records) != set(STEPS):
        raise Refused(f"{rel(run_dir / SUMMARY, root)}: the {route} route's records are "
                      f"{sorted(records)}")
    release = manifest(run_dir, summary)
    install, why = check_install(records["install"][1], route, target, table,
                                 dig(summary, "zip", "sha256"))
    why["manual_steps"] = expected_steps(route, entry, lay)
    why["launched"], launch_kept = check_launch(records["launch"][1], run_dir, route, target, install,
                                                summary, table, release, lay)
    finished = records["launch"][1].get("finished")
    instant(finished, f"the {route} route's launch record's finished")
    verdict = {"hash_checked": install["verdict"]["hash_checked"],
               "installed": install["verdict"]["installed"],
               "manual_steps": 0 if not why["manual_steps"] else None,
               "launched": not why["launched"]}
    if whole:
        verdict = {"hash_checked": False, "installed": False, "manual_steps": None, "launched": False}
    problems = whole + [f"{clause}: {p}" for clause, ps in why.items() for p in ps]
    passed = (verdict == {"hash_checked": True, "installed": True, "manual_steps": 0, "launched": True}
              and not problems)
    tool = dig(records["launch"][1], "tool") or {}
    return {"route": route, "passed": passed, "verdict": verdict, "finished": finished,
            "problems": problems, "sources_differ": sources_differ(release[0], root) if release[0] else
            ["the run folder holds no release manifest"],
            "first_pass_evidence": {
                "target": target, **({"runner": lay.runner} if lay.windows else {}),
                "summary": rel(run_dir / SUMMARY, root),
                "summary_sha256": sha256_file(run_dir / SUMMARY),
                "records": {n: sha256_file(p) for n, (p, _d) in records.items()},
                "binaries": {"launcher": tool.get("sha256"), "installer": tool.get("installer_sha256"),
                             "game": tool.get("game_sha256"), "player": tool.get("player_sha256"),
                             "release_manifest": tool.get("release_manifest_sha256")}},
            "evidence": {"summary": rel(run_dir / SUMMARY, root),
                         "summary_sha256": sha256_file(run_dir / SUMMARY),
                         **({"runner": summary.get("runner"), "release_at": lay.release_at,
                             "out_at": lay.out_at} if lay.windows else {}),
                         "records": {n: {"path": rel(p, root), "sha256": sha256_file(p)}
                                     for n, (p, _d) in records.items()},
                         "sandbox": {"home": entry.get("home"),
                                     "shared_with_host": summary.get("shared_with_host"),
                                     "binds_read_only": entry.get("binds_read_only"),
                                     "binds_read_write": entry.get("binds_read_write", []),
                                     "asserted_not_visible": entry.get("asserted_not_visible"),
                                     "write_probe": entry.get("write_probe"),
                                     **({"user_config_hidden": entry["user_config_hidden"]}
                                        if entry.get("user_config_hidden") else {})},
                         "install": install["kept"], "launch": launch_kept,
                         "zip": summary.get("zip") if route == "zip" else None}}


def read_runs(dirs: list[Path], table: dict, root: Path) -> list[dict]:
    runs = []
    for d in dirs:
        summary = load(d / SUMMARY, "a fresh-routes summary")
        if summary.get("schema") != SUMMARY_SCHEMA:
            raise Refused(f"{rel(d / SUMMARY, root)} has schema {summary.get('schema')!r}")
        target = summary.get("target")
        if target not in TARGETS:
            raise Refused(f"{rel(d / SUMMARY, root)} ran on {target!r}, not one of {TARGETS}")
        lay = layout(summary, rel(d / SUMMARY, root))
        for entry in summary.get("routes") or []:
            if not isinstance(entry, dict) or entry.get("route") not in ROUTES:
                raise Refused(f"{rel(d / SUMMARY, root)} holds a route {entry!r}")
            run = route_run(d, summary, entry, target, table, root, lay)
            run["target"] = target
            runs.append(run)
    return runs


# ---------------------------------------------------------------- the receipt

def run_dirs(given: list[Path], previous: dict | None, root: Path, reset: bool) -> list[Path]:
    """The run folders the receipt is a function of: those the previous receipt cites (unless
    `reset`), every one still on disk, then those given; each once."""
    dirs: list[Path] = []
    cited = [] if reset or not isinstance(previous, dict) else dig(previous, "inputs", "runs") or []
    for shown in cited:
        d = unrel(str(shown), root).parent
        if not (d / SUMMARY).is_file():
            raise Refused(f"the receipt cites the run {shown}, which is gone: restore it, or pass "
                          "--reset to start the first passes again from the runs given")
        dirs.append(d.resolve())
    for d in given:
        if d.resolve() not in dirs:
            dirs.append(d.resolve())
    return dirs


def build(dirs: list[Path], previous: dict | None, root: Path = ROOT, reset: bool = False) -> dict:
    table = table_identity(root)
    dirs = run_dirs(dirs, previous, root, reset)
    runs = read_runs(dirs, table, root)
    if not runs:
        raise Refused("no route run in the inputs")
    routes: dict = {}
    for route in ROUTES:
        per = {}
        for target in TARGETS:
            mine = sorted((r for r in runs if r["route"] == route and r["target"] == target),
                          key=lambda r: instant(r["finished"], "finished"))
            if not mine:
                continue            # absent: no run read now explains an entry
            last = mine[-1]
            problems = list(last["problems"])
            if last["sources_differ"]:
                problems.append("the release this run used was built from sources that are not the "
                                f"tree's: {last['sources_differ']}")
            current_pass = last["passed"] and not last["sources_differ"]
            firsts = [r for r in mine if r["passed"]]
            first = firsts[0] if firsts else None
            entry = dict(last["verdict"])
            if last["sources_differ"]:
                entry.update({"hash_checked": False, "installed": False, "manual_steps": None,
                              "launched": False})
            entry["first_passed"] = instant(first["finished"], "finished").isoformat() if first else None
            entry["first_pass_evidence"] = first["first_pass_evidence"] if first else None
            entry["last_run"] = {"finished": last["finished"], "passed": current_pass,
                                 "problems": problems, "sources_current": not last["sources_differ"]}
            entry["runs_read"] = len(mine)
            entry["evidence"] = last["evidence"]
            per[target] = entry
        if per:
            routes[route] = per
    files = pipeline_files(root)
    missing = [p for p in files if not (root / p).is_file()]
    if missing:
        raise Refused(f"the pipeline's tools are absent: {missing}")
    pins = {p: sha256_file(root / p) for p in files}
    pending = [t for t in TARGETS
               if not all(isinstance(routes.get(r), dict) and t in routes[r] for r in ROUTES)]
    return {
        "schema": SCHEMA,
        "id": "s7-installer-v1",
        "generated_by": TOOL,
        "question": "Does the installer take the game from a Steam install found automatically, a "
                    "selected unmodified game folder and a ZIP of the user's files, each "
                    "hash-checked, installed with no manual step and launched, on x86-64 Linux and "
                    "then x86-64 Windows, with the DRM-unwrap measurement first?",
        "authority": "the user's G7 bar, Documentation/decisions/2026-10-10-G5-close-answers.md, "
                     "answer 1; the routes of Documentation/decisions/2026-10-07-release-order-"
                     "and-installer.md; the contract of Documentation/scripts/check_s7_exit.py "
                     "installer",
        "command": "source tools/env.sh && .venv/bin/python " + TOOL + " " +
                   " ".join(f"--runs {rel(d, root)}" for d in dirs),
        "inputs": {"pipeline": pins,
                   "table": {k: v for k, v in table.items() if k != "paths"},
                   "min_frames": MIN_FRAMES,
                   "runs": [rel(d / SUMMARY, root) for d in dirs]},
        INSTALLER_DRM: drm_cited(root),
        "routes": routes,
        "targets_pending": {t: "no route run on this target yet; the predicate reads unmet until "
                               "its records exist" for t in pending},
        "written": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def comparable(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k not in IGNORED_IN_CHECK}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--runs", type=Path, action="append", default=[],
                    help="a folder fresh_host_routes.py --out wrote (repeatable)")
    ap.add_argument("--out", type=Path, default=None, help=f"default <root>/{RECEIPT}")
    ap.add_argument("--check", action="store_true",
                    help="recompute from the runs the receipt cites and compare; write nothing")
    ap.add_argument("--reset", action="store_true",
                    help="read only --runs, not the runs the receipt on disk cites (first passes "
                         "start again)")
    ap.add_argument("--root", type=Path, default=ROOT,
                    help="the tree whose receipts and tools are read (default this checkout)")
    a = ap.parse_args(argv)
    root = a.root.resolve()
    out = root / RECEIPT if a.out is None else a.out if a.out.is_absolute() else root / a.out
    previous = None
    if out.is_file():
        try:
            previous = json.loads(out.read_text(encoding="utf-8"))
        except ValueError:
            print(f"INSTALLER_RECEIPT_FAIL {rel(out, root)} is not JSON", file=sys.stderr)
            return 2
    if a.check and (previous is None or a.reset):
        print(f"INSTALLER_RECEIPT_FAIL --check needs the receipt {rel(out, root)} and no --reset",
              file=sys.stderr)
        return 2
    if not a.check and not a.runs:
        print("INSTALLER_RECEIPT_FAIL name the runs to read (--runs)", file=sys.stderr)
        return 2
    try:
        doc = build([d.resolve() for d in a.runs], previous, root, a.reset)
    except Refused as why:
        print(f"INSTALLER_RECEIPT_FAIL {why}", file=sys.stderr)
        return 2
    if a.check:
        if comparable(previous) != comparable(doc):
            print(f"INSTALLER_RECEIPT_STALE {rel(out, root)} differs from its recomputation",
                  file=sys.stderr)
            return 1
        print(f"INSTALLER_RECEIPT_CURRENT {rel(out, root)}")
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".part")
    tmp.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    tmp.replace(out)
    lines = []
    for route in ROUTES:
        for target in TARGETS:
            e = (doc["routes"].get(route) or {}).get(target)
            if e:
                lines.append(f"{route} on {target}: " +
                             ", ".join(f"{k} {e[k]}" for k in VERDICT) +
                             f", first passed {e['first_passed']}")
    print("\n".join(lines))
    print(f"INSTALLER_RECEIPT_OK {rel(out, root)}; pending: "
          f"{', '.join(doc['targets_pending']) or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
