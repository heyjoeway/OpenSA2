"""The installer receipt's writer (tools/s7/installer_receipt.py), a measuring instrument: each
clause it recomputes shown holding and shown failing, on synthetic route runs.

A synthetic tree holds what the writer and G7's predicate read (a small install-hashes receipt, the
DRM-unwrap measurement and the user's ruling as tracked, the pipeline's tools and the launcher's and
installer's sources), and each run folder is what host/launch/fresh_host_routes.py writes:
`fresh-routes.runrec` naming an install record and a launch record per route, each hashed; each
route's session folder with the game's run record, the observer's table and the player's report,
each hashed by the launch record; and the release's manifest, hashed by the summary. What each case
pins:

* every route passed on x86-64 Linux reads each clause true, first passed at the launch's end,
  the Windows entries absent, so check_s7_exit.installer reads unmet for x86_64-windows alone;
* the DRM measurement cited with its bytes and the ruling;
* nothing carried on trust: the receipt is a function of the run folders it cites, so a typed
  Windows entry or a first pass moved earlier reads stale, and a cited run that is gone is refused
  (or dropped with --reset);
* the earliest first pass kept across runs, a failing later run keeping it, a route not run in the
  newest folder kept from the folder that ran it;
* each clause falsified on its own by the evidence that falsifies it, whatever the record's own
  flag says, one case per clause; a run on an unclean sandbox, or with a write into its source,
  evidence of nothing; a release built from sources the tree no longer holds is not current;
* a record, or a session file, whose bytes are not the ones hashed refused; --check current and
  stale;
* the Windows runs (host/launch/windows_routes.py): Wine runs write the x86_64-windows entries with
  their runner kept, and the predicate reads met with Linux first; a native run after them keeps
  Wine's first pass and reads native; a summary whose runner is not its target's, or whose places
  are not its runner's, is refused; each runner's fresh-host rule (HOME, the write probe) holds on
  its own; a Wine folder route on a folder the sandbox does not bind has a manual step; and the
  binaries are held to the manifest's .exe names.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
TOOL = ROOT / "tools" / "s7" / "installer_receipt.py"
CHECK = ROOT / "Documentation" / "scripts" / "check_s7_exit.py"


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


R = module(TOOL, "installer_receipt_under_test")
X = module(CHECK, "check_s7_exit_for_installer_receipt")
HASHES = {"schema": "sa2-recomp-receipt/v1", "id": "install-hashes-v1", "modded": False,
          "files": [{"path": f"resource/f{i}", "bytes": 1, "sha256": "0" * 64} for i in range(4)] +
                   [{"path": "sonic2app.exe", "bytes": 1, "sha256": "0" * 64}]}
LAUNCHER, INSTALLER, GAME, PLAYER, ARCHIVE = ("1" * 64, "b" * 64, "2" * 64, "3" * 64, "d" * 64)
DRM_WRITTEN = X.instant(json.loads((ROOT / X.DRM_RECEIPT).read_text())["written"], "written")
# Instants after the live DRM-unwrap measurement, whenever it was last re-measured: the predicate
# holds every route's first pass to come after it.
def _after_measurement(hours: int) -> str:
    import json as _json
    from datetime import datetime as _dt, timedelta as _td
    held = _json.loads((Path(__file__).resolve().parents[3] /
                        "Documentation/receipts/s7-drm-unwrap-v1.json").read_text())["written"]
    return (_dt.fromisoformat(held) + _td(hours=hours)).isoformat()


T1, T2, T3 = _after_measurement(1), _after_measurement(2), _after_measurement(3)
GAME_DIR = "~/.local/share/Steam/steamapps/common/Sonic Adventure 2"
TICKS = ("# sa2 launch observer v1\nT\ttick\tpresents\tgame_mode\tcurrent_menu\n"
         "T\t50\t50\t14\t0\nT\t350\t350\t14\t0\nK\t360\t1\t1\t1\t1\nT\t400\t400\t14\t9\n"
         "K\t500\t2\t3\t3\t1\nT\t520\t520\t14\t1\n")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture
def tree(tmp_path) -> Path:
    """The files the writer and the predicate read, the measurement and ruling as tracked."""
    root = tmp_path / "tree"
    for rel in (X.DRM_RECEIPT, R.RULING, X.DRM_PINS, X.DRM_TOOL, X.DRM_QUESTION, *R.pipeline_files(ROOT)):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, root / rel)
    (root / R.HASHES).write_text(json.dumps(HASHES))
    return root


def install_record(root: Path, route: str) -> dict:
    kind = R.SOURCE_KIND[route]
    rec = {"schema": R.INSTALL_SCHEMA, "tool": {"name": "sa2-install", "sha256": INSTALLER},
           "target": "x86_64-linux", "route": route,
           "source": {"kind": kind, "path": "~/g", "read_root": GAME_DIR},
           "table": {"id": "install-hashes-v1", "receipt_sha256": sha(root / R.HASHES), "entries": 5,
                     "exe_check": {"kind": "per-section", "ruling": R.RULING}},
           "counts": {"entries": 5, "ok": 5, "substituted": 0, "missing": 0, "modified": 0,
                      "extras_skipped": 1, "extras_refused": 0},
           "extras": [{"path": "resource/gd_PC/StageSelectConfig.ini", "refused": False,
                       "why": "user config"}],
           "source_manifest": {"untouched": True, "before": "a" * 64, "after": "a" * 64},
           "hash_checked": True, "installed": True, "verified_mode": "full",
           "manual_steps": 0, "launched": False, "exit": {"code": 0, "name": "accepted"}}
    if route == "zip":
        rec["zip"] = {"root": "Sonic Adventure 2/", "files_verified": True, "methods": [8]}
        rec["source_manifest"] = {"untouched": True, "before": f"sha256={ARCHIVE} bytes=9 mtime_ns=1",
                                  "after": f"sha256={ARCHIVE} bytes=9 mtime_ns=1"}
    return rec


def game_record(route: str) -> dict:
    session = f"/out/{route}-session"
    opened = [{"rel": "sonic2app.exe"}, {"rel": "resource/f1"}, {"rel": "Config/UserConfig.cfg"},
              {"rel": "resource/gd_PC/SAVEDATA/SONIC2B__S01"}]
    res = [{"rel": o["rel"], "status": "ok"} for o in opened] + \
          [{"rel": "Config/StageSelectConfig.ini", "status": "not-found"}]
    return {"ok": True, "trap": {"note": "the guest called ExitProcess(0)"},
            "d3d9": {"closed_world": {"mode": "trap", "automated_session": True, "unlisted_methods": 0,
                                      "unlisted_calls": 0}},
            "exe": {"kind": "shipped", "overlay": {"loaded": True}},
            "files": {"ready": True, "read_root": "/home/u/.local/share/Steam/steamapps/common/Sonic Adventure 2",
                      "write_root": f"{session}/overlay", "stats": {"resolves": len(res), "opens": len(opened)},
                      "resolutions": res, "opened": opened}}


def launch_record(route: str, finished: str, root: Path) -> dict:
    session = f"/out/{route}-session"
    return {"schema": R.LAUNCH_SCHEMA, "mode": "selftest", "target": "x86_64-linux", "host": "sdl3-gpu",
            "tool": {"sha256": LAUNCHER, "release_manifest_sha256": None, "installer_sha256": INSTALLER,
                     "game_sha256": GAME, "player_sha256": PLAYER},
            "table": {"id": "install-hashes-v1", "receipt_sha256": sha(root / R.HASHES)},
            "config": {"source_kind": R.SOURCE_KIND[route], "read_root": GAME_DIR},
            "verify": {"ran": True, "exit": 0},
            "game": {"record": f"{session}/game.runrec", "ticks": f"{session}/ticks.tsv",
                     "exe": {"kind": "shipped", "overlay_loaded": True}, "opened": 4,
                     "opened_not_in_table": [], "table_paths_not_found": []},
            "player": {"exit": 0, "cancelled": False, "report": f"{session}/player-report.runrec",
                       "video_driver": "offscreen", "window": "none (offscreen)", "presents": 600},
            "relay": {"bytes_in": 9600, "bytes_out": 9600, "bytes_dropped": 0, "frames_in": 600,
                      "frames_out": 600, "framing_problem": ""},
            "selftest": {"frames": 300, "write_root": f"{session}/overlay", "read_root_saves": [],
                         "enters": [{"dik": 28, "read": True, "released": True, "tick": 350, "read_tick": 360,
                                     "current_menu": 0},
                                    {"dik": 28, "read": True, "released": True, "tick": 490, "read_tick": 500,
                                     "current_menu": 9}]},
            "failures": [], "launched": True, "finished": finished}


def manifest_text(root: Path) -> str:
    head = {"schema": "sa2-s7-release/v1",
            "sources": {p: sha(root / p) for p in ("host/launch/sa2_launch.cpp", "host/install/sa2_install_cli.cpp")}}
    lines = ["# " + json.dumps(head)]
    for path, digest in (("bin/sa2recomp", LAUNCHER), ("bin/sa2-install", INSTALLER), ("bin/sa2-game", GAME),
                         ("bin/sa2_player_gpu", PLAYER)):
        lines.append(f"{digest}  10  {path}")
    return "\n".join(lines) + "\n"


def run_dir(root: Path, where: Path, finished: str, routes=R.ROUTES, edit=None, summary_edit=None) -> Path:
    """A fresh_host_routes.py output folder with every route passing, then `edit(route, docs)`
    (docs: install, launch, game, report, ticks, entry) applied before anything is hashed, and
    `summary_edit(entry)` after."""
    where.mkdir(parents=True)
    (where / R.MANIFEST).write_text(manifest_text(root))
    entries = []
    for route in routes:
        docs = {"install": install_record(root, route), "launch": launch_record(route, finished, root),
                "game": game_record(route), "report": {"complete": True, "exit_status": 0, "presents": 600},
                "ticks": TICKS}
        game_path = "~/.local/share/Steam/steamapps/common/Sonic Adventure 2"
        source = {"steam-autodiscovery": [], "folder": [game_path],
                  "zip": ["~/Downloads/Sonic Adventure 2.zip"]}[route]
        docs["entry"] = {
            "route": route, "home": "an empty tmpfs", "sandbox_clean": True, "write_probe": "refused",
            "binds_read_only": [{"source": game_path, "at": game_path},
                                {"source": "~/z.zip", "at": "~/Downloads/Sonic Adventure 2.zip"}],
            "asserted_not_visible": ["~/OpenSA2"], "pass": True,
            "steps": [{"name": "install", "argv": ["/opt/sa2recomp/bin/sa2-install", "--source",
                                                   R.SOURCE_KIND[route], *source, "--yes", "--record",
                                                   f"/out/{route}-install.runrec"]},
                      {"name": "launch", "argv": ["/opt/sa2recomp/bin/sa2recomp", "--selftest", "--frames",
                                                  "300", "--record", f"/out/{route}-launch.runrec",
                                                  "--out", f"/out/{route}-session"]}]}
        if edit:
            edit(route, docs)
        session = where / f"{route}-session"
        session.mkdir()
        (session / "game.runrec").write_text(json.dumps(docs["game"]))
        (session / "ticks.tsv").write_text(docs["ticks"])
        (session / "player-report.runrec").write_text(json.dumps(docs["report"]))
        launch = docs["launch"]
        for key, name in (("game", "game.runrec"), ("player", "player-report.runrec")):
            launch[key].setdefault("record_sha256" if key == "game" else "report_sha256", sha(session / name))
        launch["game"].setdefault("ticks_sha256", sha(session / "ticks.tsv"))
        if launch["tool"].get("release_manifest_sha256", 0) is None:
            launch["tool"]["release_manifest_sha256"] = sha(where / R.MANIFEST)
        entry = docs["entry"]
        for step, name in zip(entry["steps"], ("install", "launch")):
            p = where / f"{route}-{name}.runrec"
            p.write_text(json.dumps(docs[name]))
            step.update({"exit": 0, "expected_exit": 0, "record": p.name, "record_sha256": sha(p), "pass": True})
        if summary_edit:
            summary_edit(entry)
        entries.append(entry)
    summary = {"schema": R.SUMMARY_SCHEMA, "target": "x86_64-linux",
               "release": {"manifest_sha256": sha(where / R.MANIFEST)}, "routes": entries,
               "shared_with_host": ["/usr"], "zip": {"sha256": ARCHIVE, "bytes": 9}}
    (where / R.SUMMARY).write_text(json.dumps(summary))
    return where


def write(tree: Path, doc: dict) -> None:
    (tree / X.INSTALLER_RECEIPT).parent.mkdir(parents=True, exist_ok=True)
    (tree / X.INSTALLER_RECEIPT).write_text(json.dumps(doc, indent=1) + "\n")


def test_every_route_passed_on_linux_and_windows_still_pending(tree, tmp_path):
    doc = R.build([run_dir(tree, tmp_path / "r1", T1)], None, tree)
    assert doc["schema"] == X.INSTALLER_SCHEMA and doc["generated_by"] == R.TOOL
    for route in X.INSTALLER_ROUTES:
        e = doc["routes"][route]["x86_64-linux"]
        assert {k: e[k] for k in X.INSTALLER_RUN} == X.INSTALLER_RUN
        assert type(e["manual_steps"]) is int
        assert X.instant(e["first_passed"], "first") == X.instant(T1, "t1")
        assert e["last_run"] == {"finished": T1, "passed": True, "problems": [], "sources_current": True}
        assert e["first_pass_evidence"]["binaries"] == {"launcher": LAUNCHER, "installer": INSTALLER,
                                                        "game": GAME, "player": PLAYER,
                                                        "release_manifest": sha(tmp_path / "r1" / R.MANIFEST)}
        assert e["first_pass_evidence"]["target"] == "x86_64-linux"
        assert e["evidence"]["launch"]["observed"] == {"title_first_tick": 50, "title_held_tick": 350,
                                                       "main_menu_tick": 520, "key_polls": 2}
        assert "x86_64-windows" not in doc["routes"][route]
    assert list(doc["targets_pending"]) == ["x86_64-windows"]
    cited = doc["drm_unwrap"]
    assert cited["receipt"] == X.DRM_RECEIPT and cited["bytes_match"] is False
    assert cited["ruling"] == R.RULING
    assert set(doc["inputs"]["pipeline"]) == set(R.pipeline_files(tree))
    assert "host/launch/sa2_launch.cpp" in doc["inputs"]["pipeline"]
    write(tree, doc)
    result = X.installer(tree)
    assert not result.met
    problems = result.detail.split("; ")
    assert sorted(problems) == sorted(f"{r} on x86_64-windows: hash_checked, installed, manual_steps, "
                                      f"launched not as required" for r in X.INSTALLER_ROUTES)


def test_typed_windows_entries_read_met_to_the_predicate_and_stale_to_check(tree, tmp_path, capsys):
    """The Linux half is to the predicate's contract: with Windows entries added it reads met. But
    no run explains entries added by hand, so --check reads the receipt stale."""
    d = run_dir(tree, tmp_path / "r1", T1)
    assert R.main(["--root", str(tree), "--runs", str(d)]) == 0
    doc = json.loads((tree / R.RECEIPT).read_text())
    for route in X.INSTALLER_ROUTES:
        doc["routes"][route]["x86_64-windows"] = dict(doc["routes"][route]["x86_64-linux"], first_passed=T2)
    doc["targets_pending"] = {}
    write(tree, doc)
    assert X.instant(T1, "t1") > DRM_WRITTEN
    assert X.installer(tree).met
    assert R.main(["--root", str(tree), "--check"]) == 1
    assert "INSTALLER_RECEIPT_STALE" in capsys.readouterr().err


def test_a_first_pass_moved_earlier_reads_stale(tree, tmp_path, capsys):
    d = run_dir(tree, tmp_path / "r1", T2)
    assert R.main(["--root", str(tree), "--runs", str(d)]) == 0
    assert R.main(["--root", str(tree), "--check"]) == 0
    doc = json.loads((tree / R.RECEIPT).read_text())
    doc["routes"]["zip"]["x86_64-linux"]["first_passed"] = T1
    write(tree, doc)
    assert R.main(["--root", str(tree), "--check"]) == 1
    assert "INSTALLER_RECEIPT_STALE" in capsys.readouterr().err
    # A rebuild from the cited runs restores the measured instant.
    assert R.main(["--root", str(tree), "--runs", str(d)]) == 0
    assert json.loads((tree / R.RECEIPT).read_text())["routes"]["zip"]["x86_64-linux"]["first_passed"] == \
        X.instant(T2, "t").isoformat()


def test_the_earliest_first_pass_is_kept_while_its_run_is_cited(tree, tmp_path):
    first = R.build([run_dir(tree, tmp_path / "r1", T2)], None, tree)
    later = R.build([run_dir(tree, tmp_path / "r2", T3)], first, tree)
    e = later["routes"]["zip"]["x86_64-linux"]
    assert e["first_passed"] == X.instant(T2, "t").isoformat() and e["runs_read"] == 2
    assert e["last_run"]["finished"] == T3
    assert later["inputs"]["runs"] == [R.rel(tmp_path / d / R.SUMMARY, tree) for d in ("r1", "r2")]
    both = R.build([run_dir(tree, tmp_path / "r3", T3), run_dir(tree, tmp_path / "r4", T1)], None, tree)
    e = both["routes"]["folder"]["x86_64-linux"]
    assert e["first_passed"] == X.instant(T1, "t").isoformat() and e["runs_read"] == 2
    assert e["last_run"]["finished"] == T3


def test_a_cited_run_that_is_gone_is_refused_unless_reset(tree, tmp_path):
    first = R.build([run_dir(tree, tmp_path / "r1", T1)], None, tree)
    (tmp_path / "r1" / R.SUMMARY).rename(tmp_path / "r1" / "moved")
    r2 = run_dir(tree, tmp_path / "r2", T3)
    with pytest.raises(R.Refused, match="which is gone"):
        R.build([r2], first, tree)
    doc = R.build([r2], first, tree, reset=True)
    assert doc["routes"]["zip"]["x86_64-linux"]["first_passed"] == X.instant(T3, "t").isoformat()
    assert doc["inputs"]["runs"] == [R.rel(r2 / R.SUMMARY, tree)]


def test_a_failing_later_run_reads_false_and_keeps_the_first_pass(tree, tmp_path):
    first = R.build([run_dir(tree, tmp_path / "r1", T1)], None, tree)

    def broken(route, docs):
        if route == "zip":
            docs["game"]["files"]["opened"].append({"rel": "resource/gd_PC/StageSelectConfig.ini"})
            docs["game"]["files"]["stats"]["opens"] += 1
    doc = R.build([run_dir(tree, tmp_path / "r2", T2, edit=broken)], first, tree)
    e = doc["routes"]["zip"]["x86_64-linux"]
    assert e["launched"] is False and e["hash_checked"] is True
    assert e["first_passed"] == X.instant(T1, "t").isoformat()
    assert e["last_run"]["passed"] is False
    assert any("StageSelectConfig.ini" in p for p in e["last_run"]["problems"])


def test_a_route_not_run_again_keeps_its_entry_from_the_run_that_ran_it(tree, tmp_path):
    first = R.build([run_dir(tree, tmp_path / "r1", T1)], None, tree)
    doc = R.build([run_dir(tree, tmp_path / "r2", T2, routes=("zip",))], first, tree)
    assert doc["routes"]["folder"] == first["routes"]["folder"]
    assert doc["routes"]["zip"]["x86_64-linux"]["last_run"]["finished"] == T2


def _set(path: str, value):
    """An edit setting one field: `<doc>.<key>...` with docs install, launch, game, report, entry;
    a numeric key indexes a list; `ticks` replaces the observer's table."""
    def edit(route, docs):
        if path == "ticks":
            docs["ticks"] = value
            return
        keys = path.split(".")
        doc = docs[keys[0]]
        for k in keys[1:-1]:
            doc = doc[int(k)] if k.isdigit() else doc[k]
        last = keys[-1]
        if last.isdigit():
            doc[int(last)] = value
        else:
            doc[last] = value
    return edit


def _append(path: str, item):
    def edit(route, docs):
        doc = docs
        for k in path.split("."):
            doc = doc[k]
        doc.append(item)
    return edit


LAUNCH_ARGV = ["/opt/sa2recomp/bin/sa2recomp", "--selftest", "--frames", "300", "--record",
               "/out/zip-launch.runrec", "--out", "/out/zip-session"]
CLAUSES = [
    # hash_checked
    ("install.counts.missing", 1, "hash_checked", "not every table entry verified"),
    ("install.counts.ok", 4, "hash_checked", "not every table entry verified"),
    ("install.hash_checked", False, "hash_checked", "says hash_checked False"),
    ("install.verified_mode", "quick", "hash_checked", "verified in mode 'quick', not 'full'"),
    ("install.table.receipt_sha256", "f" * 64, "hash_checked", "the install's table is"),
    ("install.table.exe_check", {"kind": "whole-file"}, "hash_checked", "not per section"),
    ("install.zip.files_verified", False, "hash_checked", "zip.files_verified"),
    ("install.target", "x86_64-windows", "hash_checked", "ran on 'x86_64-windows'"),
    ("install.schema", "other/v1", "hash_checked", "the install record's schema is 'other/v1'"),
    ("install.route", "folder", "hash_checked", "the install record is the 'folder' route's"),
    ("install.tool.sha256", None, "hash_checked", "names its installer as None, not a sha256"),
    # installed
    ("install.exit", {"code": 3, "name": "refused"}, "installed", "the install exited 3"),
    ("install.installed", False, "installed", "installed False"),
    ("install.source_manifest.after", "sha256=" + "9" * 64 + " bytes=9 mtime_ns=2", "installed",
     "its manifests before and after differ"),
    ("install.source_manifest.before", "sha256=" + "9" * 64 + " bytes=9", "installed",
     "not the one the summary names"),
    ("install.manual_steps", 1, "installed", "counts manual steps as 1"),
    # manual_steps, from the summary's steps
    ("entry.steps.0.argv", ["/opt/sa2recomp/bin/sa2-install", "--source", "zip", "~/elsewhere.zip", "--yes",
                            "--record", "/out/zip-install.runrec"], "manual_steps", "the install step ran"),
    ("entry.steps.0.argv", ["/opt/sa2recomp/bin/sa2-install", "--source", "zip", "~/Downloads/Sonic Adventure 2.zip",
                            "--yes", "--no-verify", "--record", "/out/zip-install.runrec"],
     "manual_steps", "the install step ran"),
    ("entry.steps.1.argv", LAUNCH_ARGV[:3] + ["1"] + LAUNCH_ARGV[4:], "manual_steps", "the launch step ran"),
    ("entry.steps.1.argv", LAUNCH_ARGV + ["--no-verify"], "manual_steps", "the launch step ran"),
    # launched: the launch record
    ("launch.launched", False, "launched", "says launched False"),
    ("launch.failures", ["the title was never held"], "launched", "failures ['the title was never held']"),
    ("launch.mode", "play", "launched", "a 'play' session's, not a selftest's"),
    ("launch.schema", "other/v1", "launched", "the launch record's schema is 'other/v1'"),
    ("launch.target", "x86_64-windows", "launched", "the launch ran on 'x86_64-windows'"),
    ("launch.verify.ran", False, "launched", "not verified at launch"),
    ("launch.verify.exit", 3, "launched", "the verify at launch exited 3"),
    ("launch.table.id", "other", "launched", "the launcher's table is not"),
    ("launch.config.source_kind", "folder", "launched", "the launch read a 'folder' installation"),
    ("launch.tool.sha256", "x", "launched", "names its bin/sa2recomp as 'x', not a sha256"),
    ("launch.tool.game_sha256", "e" * 64, "launched", "the bin/sa2-game that ran is not the release manifest's"),
    ("launch.tool.player_sha256", None, "launched", "names its bin/sa2_player_gpu as None"),
    ("launch.tool.release_manifest_sha256", "e" * 64, "launched", "another release than the summary names"),
    ("launch.tool.installer_sha256", "e" * 64, "launched", "not the one that installed"),
    ("launch.selftest.frames", 1, "launched", "held the title for 1 frames, fewer than 300"),
    ("launch.selftest.enters", [{"dik": 28, "read": False}], "launched", "Enters were not all read"),
    ("launch.selftest.enters", [{"dik": 0x1D, "read": True, "released": True, "tick": 1, "read_tick": 2}],
     "launched", "Enters were not all read"),
    ("launch.selftest.enters", [], "launched", "Enters were not all read"),
    ("launch.selftest.write_root", "~/.local/share/sa2recomp/overlay", "launched",
     "not its session's own overlay"),
    ("launch.relay.bytes_dropped", 5, "launched", "the relay did not hand on every byte"),
    ("launch.relay.frames_out", 599, "launched", "the relay did not hand on every byte"),
    ("launch.relay.framing_problem", "a record of size 6", "launched", "the relay did not hand on every byte"),
    ("launch.player.exit", 3, "launched", "the player exited 3"),
    ("launch.player.cancelled", True, "launched", "cancelled True"),
    # launched: the game's own run record
    ("game.ok", False, "launched", "the game's run record says ok false"),
    ("game.trap.note", "a trap fired at 0x401000", "launched", "the guest did not end itself"),
    ("game.d3d9.closed_world.mode", "audit", "launched", "the closed world ran in mode 'audit'"),
    ("game.d3d9.closed_world.automated_session", False, "launched", "not run as an automated session"),
    ("game.d3d9.closed_world.unlisted_calls", 2, "launched", "off the M7 list"),
    ("game.exe.kind", "unpacked", "launched", "the 'unpacked' executable"),
    ("game.exe.overlay.loaded", False, "launched", "not the shipped one through the overlay"),
    ("game.files.ready", False, "launched", "the game's file system read"),
    ("game.files.write_root", "/home/u/.local/share/sa2recomp/overlay", "launched", "the game's file system read"),
    ("game.files.read_root", "/elsewhere", "launched", "the game's file system read"),
    ("game.files.stats.opens", 999, "launched", "the file log was truncated"),
    ("game.files.stats.resolves", 999, "launched", "the file log was truncated"),
    # launched: the observer's table and the player's report
    ("ticks", TICKS.replace("T\t350\t350\t14\t0\n", ""), "launched", "never holds the title for 300 frames"),
    ("ticks", TICKS.replace("T\t520\t520\t14\t1\n", ""), "launched", "never reaches the main menu"),
    ("ticks", "\n".join(x for x in TICKS.split("\n") if not x.startswith("K")), "launched",
     "no key the game read"),
    ("report.complete", False, "launched", "the player's report is complete False"),
    ("report.exit_status", 1, "launched", "with exit status 1"),
    ("report.presents", 10, "launched", "the player presented 10 of the 600 frames"),
]


@pytest.mark.parametrize("path, value, clause, words", CLAUSES,
                         ids=[f"{c[0]}={str(c[1])[:24]}" for c in CLAUSES])
def test_each_clause_is_falsified_by_its_evidence(tree, tmp_path, path, value, clause, words):
    route = "zip"
    edit = _set(path, value)

    def only_zip(r, docs):
        if r == route:
            edit(r, docs)
    doc = R.build([run_dir(tree, tmp_path / "r1", T1, edit=only_zip)], None, tree)
    e = doc["routes"][route]["x86_64-linux"]
    assert e[clause] is not X.INSTALLER_RUN[clause] or (clause == "manual_steps" and e[clause] is None)
    assert e["first_passed"] is None and e["last_run"]["passed"] is False
    assert any(words in p for p in e["last_run"]["problems"]), e["last_run"]["problems"]
    for other in X.INSTALLER_ROUTES:
        if other != route:
            assert doc["routes"][other]["x86_64-linux"]["last_run"]["passed"] is True


@pytest.mark.parametrize("item, words", [
    ({"rel": "resource/gd_PC/StageSelectConfig.ini"}, "files opened that the table does not hold"),
    ({"rel": "resource/f2", "status": "not-found"}, "table paths not found: ['resource/f2']"),
    ({"rel": "../../etc/passwd", "status": "escape"}, "paths refused: ['../../etc/passwd (escape)']"),
], ids=["opened-not-in-table", "table-path-not-found", "path-refused"])
def test_each_file_clause_is_recomputed_from_the_games_record(tree, tmp_path, item, words):
    """The launch record's own lists say nothing is wrong; the game's run record says otherwise."""
    key = "opened" if "status" not in item else "resolutions"

    def edit(route, docs):
        if route == "zip":
            docs["game"]["files"][key].append(item)
            docs["game"]["files"]["stats"]["opens" if key == "opened" else "resolves"] += 1
    doc = R.build([run_dir(tree, tmp_path / "r1", T1, edit=edit)], None, tree)
    e = doc["routes"]["zip"]["x86_64-linux"]
    assert e["launched"] is False
    assert any(words in p for p in e["last_run"]["problems"]), e["last_run"]["problems"]


def test_a_session_file_not_the_one_hashed_is_no_evidence(tree, tmp_path):
    d = run_dir(tree, tmp_path / "r1", T1)
    (d / "zip-session" / "ticks.tsv").write_text(TICKS + "T\t600\t600\t14\t1\n")
    e = R.build([d], None, tree)["routes"]["zip"]["x86_64-linux"]
    assert e["launched"] is False
    assert "launched: the observer's table is not the file the launch record hashed" in e["last_run"]["problems"]


def test_a_release_manifest_not_the_summarys_is_no_evidence(tree, tmp_path):
    d = run_dir(tree, tmp_path / "r1", T1)
    (d / R.MANIFEST).write_text(manifest_text(tree) + "# changed\n")
    e = R.build([d], None, tree)["routes"]["folder"]["x86_64-linux"]
    assert e["launched"] is False
    assert any("release.manifest is not the one the summary names" in p for p in e["last_run"]["problems"])


def test_a_release_of_other_sources_is_not_current(tree, tmp_path):
    """The last run's release was built from sources the tree no longer holds: every clause reads
    false until the routes run again, and the first pass, a historical fact, stays."""
    d = run_dir(tree, tmp_path / "r1", T1)
    with open(tree / "host/launch/sa2_launch.cpp", "a") as f:
        f.write("// changed\n")
    e = R.build([d], None, tree)["routes"]["steam-autodiscovery"]["x86_64-linux"]
    assert (e["hash_checked"], e["installed"], e["manual_steps"], e["launched"]) == (False, False, None, False)
    assert e["last_run"]["sources_current"] is False and e["last_run"]["passed"] is False
    assert any("host/launch/sa2_launch.cpp" in p for p in e["last_run"]["problems"])
    assert e["first_passed"] == X.instant(T1, "t").isoformat()


@pytest.mark.parametrize("key, value", [("sandbox_clean", False), ("write_probe", "wrote"),
                                        ("write_probe", "no-such-folder"), ("home", "the user's own")])
def test_a_run_off_a_fresh_host_is_evidence_of_nothing(tree, tmp_path, key, value):
    doc = R.build([run_dir(tree, tmp_path / "r1", T1, summary_edit=lambda e: e.update({key: value}))],
                  None, tree)
    for route in X.INSTALLER_ROUTES:
        e = doc["routes"][route]["x86_64-linux"]
        assert (e["hash_checked"], e["installed"], e["manual_steps"], e["launched"]) == \
            (False, False, None, False)
        assert e["first_passed"] is None


def test_a_record_not_the_one_its_summary_hashed_is_refused(tree, tmp_path):
    d = run_dir(tree, tmp_path / "r1", T1)
    rec = d / "folder-install.runrec"
    doc = json.loads(rec.read_text())
    rec.write_text(json.dumps(doc, indent=2))
    with pytest.raises(R.Refused, match="is not the record fresh-routes.runrec hashed"):
        R.build([d], None, tree)


def test_check_reads_current_then_stale(tree, tmp_path, capsys):
    d = run_dir(tree, tmp_path / "r1", T1)
    out = tree / R.RECEIPT
    assert R.main(["--root", str(tree), "--runs", str(d)]) == 0
    assert R.main(["--root", str(tree), "--check"]) == 0
    assert R.main(["--root", str(tree), "--runs", str(d), "--check"]) == 0
    doc = json.loads(out.read_text())
    assert doc["routes"]["zip"]["x86_64-linux"]["first_pass_evidence"]["summary_sha256"] == \
        sha(d / R.SUMMARY)
    doc["routes"]["zip"]["x86_64-linux"]["hash_checked"] = "yes"
    out.write_text(json.dumps(doc))
    assert R.main(["--root", str(tree), "--check"]) == 1
    assert "INSTALLER_RECEIPT_STALE" in capsys.readouterr().err


def test_main_refuses_without_writing(tree, tmp_path, capsys):
    d = run_dir(tree, tmp_path / "r1", T1)
    (d / "zip-launch.runrec").write_text("{}")
    assert R.main(["--root", str(tree), "--runs", str(d)]) == 2
    assert "INSTALLER_RECEIPT_FAIL" in capsys.readouterr().err
    assert not (tree / R.RECEIPT).exists()
    assert R.main(["--root", str(tree)]) == 2
    assert R.main(["--root", str(tree), "--check"]) == 2


# ------------------------------------------------------------------ the Windows runs (windows_routes.py)

WINE_AT = {"release_at": "C:/Program Files/sa2recomp", "out_at": "O:"}
NATIVE_AT = {"release_at": "C:/Users/u/sa2recomp-windows-x86_64", "out_at": "C:/Users/u/sa2-runs/1"}


def windows_run_dir(root: Path, where: Path, finished: str, runner: str = "wine", routes=R.ROUTES,
                    summary_fix=None) -> Path:
    """A windows_routes.py output folder, every route passing: the Linux folder's records with the
    Windows places, binaries and runner, as `runner` (wine or native) writes them."""
    at = WINE_AT if runner == "wine" else NATIVE_AT
    rel_at, out_at = at["release_at"], at["out_at"]
    game_dir = "S:/steamapps/common/Sonic Adventure 2"

    def edit(route, docs):
        def swap(text: str) -> str:
            for name in ("sa2-install", "sa2recomp"):
                text = text.replace(f"/opt/sa2recomp/bin/{name}", f"{rel_at}/bin/{name}.exe")
            return text.replace("/out/", out_at + "/")
        for key in ("install", "launch", "game"):
            docs[key] = json.loads(swap(json.dumps(docs[key])))
        docs["install"]["target"] = docs["launch"]["target"] = "x86_64-windows"
        docs["launch"]["config"]["read_root"] = game_dir
        # The runtime's own spelling of its roots: backslashes are folded, case-blind.
        docs["game"]["files"]["read_root"] = game_dir.replace("/", "\\").upper()
        docs["game"]["files"]["write_root"] = docs["game"]["files"]["write_root"].lower()
        e = docs["entry"]
        argv = e["steps"][0]["argv"]
        if route == "folder":
            argv[3] = game_dir
        elif route == "zip":
            argv[3] = "C:/users/steamuser/Downloads/Sonic Adventure 2.zip"
        e["steps"] = json.loads(swap(json.dumps(e["steps"])))
        e["binds_read_only"] = [] if runner == "native" else [
            {"source": "~/.local/share/Steam/steamapps/common/Sonic Adventure 2",
             "at": "~/.local/share/Steam/steamapps/common/Sonic Adventure 2", "windows": game_dir},
            {"source": "~/z.zip", "at": "~/.wine/drive_c/users/steamuser/Downloads/Sonic Adventure 2.zip",
             "windows": "C:/users/steamuser/Downloads/Sonic Adventure 2.zip"}]
        if runner == "native":
            e["home"] = R.NATIVE_HOME
            e["write_probe"] = "refused" if route == "zip" else R.NATIVE_UNPROBED
    d = run_dir(root, where, finished, routes, edit=edit)
    text = (d / R.MANIFEST).read_text()
    for name in ("sa2recomp", "sa2-install", "sa2-game", "sa2_player_gpu"):
        text = text.replace(f"  bin/{name}\n", f"  bin/{name}.exe\n")
    (d / R.MANIFEST).write_text(text)
    manifest_sha = sha(d / R.MANIFEST)
    for route in routes:      # the launch records name the manifest; rehash what names them
        p = d / f"{route}-launch.runrec"
        rec = json.loads(p.read_text())
        rec["tool"]["release_manifest_sha256"] = manifest_sha
        p.write_text(json.dumps(rec))
    summary = json.loads((d / R.SUMMARY).read_text())
    summary.update(target="x86_64-windows", runner={"kind": runner}, **at)
    summary["release"]["manifest_sha256"] = manifest_sha
    for entry in summary["routes"]:
        entry["steps"][1]["record_sha256"] = sha(d / entry["steps"][1]["record"])
    if summary_fix:
        summary_fix(summary)
    (d / R.SUMMARY).write_text(json.dumps(summary))
    return d


def test_wine_runs_write_the_windows_entries_and_keep_their_runner(tree, tmp_path):
    doc = R.build([run_dir(tree, tmp_path / "r1", T1), windows_run_dir(tree, tmp_path / "w1", T2)], None, tree)
    for route in X.INSTALLER_ROUTES:
        e = doc["routes"][route]["x86_64-windows"]
        assert {k: e[k] for k in X.INSTALLER_RUN} == X.INSTALLER_RUN, e["last_run"]["problems"]
        assert X.instant(e["first_passed"], "first") == X.instant(T2, "t2")
        assert e["first_pass_evidence"]["runner"] == "wine"
        assert e["evidence"]["runner"] == {"kind": "wine"}
        assert e["evidence"]["release_at"] == "C:/Program Files/sa2recomp" and e["evidence"]["out_at"] == "O:"
        assert "runner" not in doc["routes"][route]["x86_64-linux"]["evidence"]
    assert doc["targets_pending"] == {}
    write(tree, doc)
    assert X.installer(tree).met


def test_a_native_run_after_wine_keeps_wines_first_pass_and_reads_native(tree, tmp_path):
    runs = [run_dir(tree, tmp_path / "r1", T1), windows_run_dir(tree, tmp_path / "w1", T2),
            windows_run_dir(tree, tmp_path / "n1", T3, runner="native")]
    doc = R.build(runs, None, tree)
    for route in X.INSTALLER_ROUTES:
        e = doc["routes"][route]["x86_64-windows"]
        assert e["last_run"]["passed"] is True, e["last_run"]["problems"]
        assert e["first_pass_evidence"]["runner"] == "wine"
        assert e["evidence"]["runner"] == {"kind": "native"} and e["runs_read"] == 2
        assert e["evidence"]["sandbox"]["write_probe"] == ("refused" if route == "zip" else R.NATIVE_UNPROBED)


@pytest.mark.parametrize("runner, fix, words", [
    ("wine", lambda s: s.update(target="x86_64-linux"), "by a 'wine' runner"),
    ("wine", lambda s: s.update(runner={"kind": "qemu"}), "by a 'qemu' runner"),
    ("wine", lambda s: s.update(out_at="Z:"), "out_at is 'Z:', not 'O:'"),
    ("wine", lambda s: s.update(release_at="Z:/home/u/release"), "release_at is 'Z:/home/u/release'"),
    ("native", lambda s: s.update(out_at="/out"), "is not an absolute Windows path"),
], ids=["wine-on-linux", "unknown-runner", "wine-out", "wine-release", "native-out"])
def test_a_windows_summary_off_its_runners_places_is_refused(tree, tmp_path, runner, fix, words):
    d = windows_run_dir(tree, tmp_path / "w1", T2, runner=runner, summary_fix=fix)
    with pytest.raises(R.Refused, match=words):
        R.build([d], None, tree)


@pytest.mark.parametrize("runner, route, key, value", [
    ("wine", "folder", "home", R.NATIVE_HOME),
    ("wine", "folder", "write_probe", R.NATIVE_UNPROBED),
    ("native", "zip", "write_probe", R.NATIVE_UNPROBED),
    ("native", "folder", "home", "an empty tmpfs"),
], ids=["wine-home-native", "wine-unprobed", "native-zip-unprobed", "native-home-tmpfs"])
def test_a_windows_run_off_its_runners_fresh_host_is_evidence_of_nothing(tree, tmp_path, runner, route,
                                                                          key, value):
    def fix(summary):
        for e in summary["routes"]:
            if e["route"] == route:
                e[key] = value
    d = windows_run_dir(tree, tmp_path / "w1", T2, runner=runner, summary_fix=fix)
    e = R.build([d], None, tree)["routes"][route]["x86_64-windows"]
    assert {k: e[k] for k in X.INSTALLER_RUN} == {"hash_checked": False, "installed": False,
                                                  "manual_steps": None, "launched": False}
    assert e["first_passed"] is None


def test_a_wine_folder_route_on_an_unbound_folder_has_a_manual_step(tree, tmp_path):
    def fix(summary):
        for e in summary["routes"]:
            e["binds_read_only"] = [b for b in e["binds_read_only"] if "Sonic Adventure 2.zip" in b["at"]]
    d = windows_run_dir(tree, tmp_path / "w1", T2, summary_fix=fix)
    doc = R.build([d], None, tree)
    assert doc["routes"]["folder"]["x86_64-windows"]["manual_steps"] is None
    assert doc["routes"]["zip"]["x86_64-windows"]["manual_steps"] == 0


def test_windows_binaries_are_held_to_the_manifests_exe_names(tree, tmp_path):
    d = windows_run_dir(tree, tmp_path / "w1", T2, routes=("folder",))
    text = (d / R.MANIFEST).read_text().replace("  bin/sa2-game.exe\n", "  bin/sa2-game\n")
    (d / R.MANIFEST).write_text(text)
    summary = json.loads((d / R.SUMMARY).read_text())
    summary["release"]["manifest_sha256"] = sha(d / R.MANIFEST)
    (d / R.SUMMARY).write_text(json.dumps(summary))
    e = R.build([d], None, tree)["routes"]["folder"]["x86_64-windows"]
    assert e["launched"] is False
    assert any("bin/sa2-game.exe that ran is not the release manifest's" in p or
               "another release" in p for p in e["last_run"]["problems"])
