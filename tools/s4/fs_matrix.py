#!/usr/bin/env python3
"""Run the guest against five file systems and check that its answers move.

Subtitle: `link_s4.py --phase run` once per column, with the overlay seeded
differently each time, plus the cross-column comparisons a single run cannot
make.

Why a tool and not five commands
--------------------------------
A single run says the guest parsed 1280x720 out of `Config/UserConfig.cfg`.
It cannot say that the number came from the file rather than from the image,
from the probe, or from a default -- and this project's recurring failure is
exactly that shape: a check whose two sides cannot disagree. So the file is
changed and the same question is asked again:

  baseline       the installation's own config, nothing in the overlay
  overlay        the same path in the overlay, with three values changed
  overlay_case   the overlay file spelled in a different case: the guest asks
                 for `Config\\UserConfig.cfg`, the overlay holds
                 `config/userconfig.cfg`, and only the case-insensitive
                 resolver can connect them
  no_case_fold   the same tree with `SA2_VFS_NO_CASE_FOLD=1`: the overlay file
                 becomes invisible and the guest must fall through to the
                 installation and get the installation's numbers back
  empty_root     an installation with nothing in it: the guest must find no
                 config, create its `Config` directory in the overlay, and
                 carry on

The verdicts that matter are *between* columns: baseline and overlay must
disagree on Width/Height/RefreshRate, overlay and no_case_fold must disagree,
and no_case_fold must equal baseline. None of those can be satisfied by a
runtime that answers every path the same way, and none can be satisfied by a
probe carrying its own answer.

Every column's JSON is kept, including a failing one.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/fs_matrix.py --name fs.next+allarms
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "s3" / "whole_image"))
import common as C  # noqa: E402

HERE = Path(__file__).resolve().parent
PY = C.ROOT / ".venv" / "bin" / "python"
RUNDIR = C.GEN / "s4" / "run"
OVERLAY = C.GEN / "s4" / "vfsroot"
EMPTY = C.GEN / "s4" / "emptyroot"
KEYS = ("Width", "Height", "RefreshRate")


def sh(cmd, **kw):
    p = subprocess.run([str(c) for c in cmd], cwd=str(C.ROOT), capture_output=True,
                       text=True, timeout=1800, **kw)
    if p.returncode:
        print("  ! " + (p.stderr or p.stdout).strip().splitlines()[-1][:300], flush=True)
    return p


def seed(kind: str, install: str) -> None:
    sh([PY, HERE / "vfs_overlay.py", "--clear", "--overlay", OVERLAY])
    if kind == "plain":
        sh([PY, HERE / "vfs_overlay.py", "--overlay", OVERLAY, "--install", install,
            "--set", "Width=1024", "--set", "Height=768", "--set", "RefreshRate=75"])
    elif kind == "cased":
        sh([PY, HERE / "vfs_overlay.py", "--overlay", OVERLAY, "--install", install,
            "--set", "Width=1024", "--set", "Height=768", "--set", "RefreshRate=75"])
        src = OVERLAY / "Config" / "UserConfig.cfg"
        dst = OVERLAY / "config" / "userconfig.cfg"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        shutil.rmtree(OVERLAY / "Config", ignore_errors=True)


def run_column(name: str, col: str, install: str, seed_kind: str, env_extra: dict,
               require_files: bool, control: str | None = None) -> dict:
    seed(seed_kind, install)
    out = RUNDIR / f"{name}.{col}.json"
    cmd = [PY, HERE / "link_s4.py", "--tag", "next+allarms", "--main", "harness/crt_probe.c",
           "--name", name, "--phase", "run",
           "--run", C.EXE, "--run=--json", "--run", out,
           "--run=--require-stop", "--run=--stop-import", "--run", "LoadIconW",
           # The served half of the escape pair has to be a path that exists in
           # *this* column's tree: on the empty root the installation's config
           # is not there, and a control that legitimately answers "no" would
           # make the pair look like a runtime that refuses everything.
           "--run=--control-path", "--run", control or "C:\\SA2\\Config\\UserConfig.cfg",
           "--env", f"SA2_INSTALL={install}", "--env", f"SA2_VFS_WRITE_ROOT={OVERLAY}"]
    if require_files:
        cmd.append("--run=--require-files")
    for k, v in env_extra.items():
        cmd += ["--env", f"{k}={v}"]
    t0 = time.time()
    sh(cmd)
    doc = json.loads(out.read_text()) if out.is_file() else {}
    f = doc.get("files", {})
    cfg = {x["key"]: x["guest"] for x in f.get("config", {}).get("fields", [])}
    host = {x["key"]: x["file"] for x in f.get("config", {}).get("fields", [])}
    return {"column": col, "json": str(out.relative_to(C.ROOT)), "seconds": round(time.time() - t0, 2),
            "ok": doc.get("ok"), "calls": doc.get("calls", {}).get("total"),
            "stop": doc.get("trap", {}).get("note", "")[:120],
            "guest_config": cfg, "file_config": host,
            "config_agrees": f.get("config", {}).get("guest_parsed_its_config"),
            "layers": [o["layer"] for o in f.get("opened", [])],
            "stats": f.get("stats", {}),
            "escape_refused": f.get("escape_test", {}).get("refused_and_served")}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="fs.next+allarms")
    ap.add_argument("--install", default="private/install")
    args = ap.parse_args()

    RUNDIR.mkdir(parents=True, exist_ok=True)
    EMPTY.mkdir(parents=True, exist_ok=True)

    cols = {}
    for col, seed_kind, env, req, control in (
        ("baseline", "none", {}, True, None),
        ("overlay", "plain", {}, True, None),
        ("overlay_case", "cased", {}, True, None),
        ("no_case_fold", "cased", {"SA2_VFS_NO_CASE_FOLD": "1"}, True, None),
        # On the empty root the only path that exists by the time the escape
        # pair runs is the directory the guest itself created in the overlay,
        # which is also the most direct evidence that it did.
        ("empty_root", "none", {}, False, "C:\\SA2\\Config"),
    ):
        install = str(EMPTY) if col == "empty_root" else args.install
        cols[col] = run_column(args.name, col, install, seed_kind, env, req, control)
        print(f"{col}: ok={cols[col]['ok']} calls={cols[col]['calls']} "
              f"cfg={ {k: cols[col]['guest_config'].get(k) for k in KEYS} } "
              f"layers={cols[col]['layers']}", flush=True)

    def cfg(c):
        return tuple(cols[c]["guest_config"].get(k) for k in KEYS)

    # Neither side of any of these can be empty: each operand is three integers
    # the guest wrote into its own configuration object, and a column that
    # produced none of them fails the `complete` test first.
    complete = all(all(v is not None for v in cfg(c))
                   for c in ("baseline", "overlay", "overlay_case", "no_case_fold"))
    checks = {
        "every_column_produced_a_config": complete,
        "overlay_shadows_the_installation": complete and cfg("overlay") != cfg("baseline"),
        "case_folded_overlay_is_found": complete and cfg("overlay_case") == cfg("overlay"),
        "without_folding_the_overlay_is_invisible":
            complete and cfg("no_case_fold") == cfg("baseline")
            and cfg("no_case_fold") != cfg("overlay_case"),
        "the_guest_agreed_with_the_file_in_every_column":
            all(cols[c]["config_agrees"] for c in
                ("baseline", "overlay", "overlay_case", "no_case_fold")),
        "the_guest_created_its_config_directory_on_an_empty_root":
            cols["empty_root"]["stats"].get("dirs_created", 0) >= 1,
        "nothing_tried_to_write_the_installation":
            all(c["stats"].get("install_write_attempts", 1) == 0 for c in cols.values()),
        "the_escape_was_refused_in_every_column":
            all(c["escape_refused"] for c in cols.values()),
        "every_column_that_required_files_passed":
            all(cols[c]["ok"] for c in ("baseline", "overlay", "overlay_case", "no_case_fold")),
    }
    verdict = {"written": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "keys": list(KEYS),
               "config_by_column": {c: cfg(c) for c in cols},
               "columns": cols, "checks": checks, "ok": all(checks.values())}
    vf = RUNDIR / f"{args.name}.matrix.json"
    vf.write_text(json.dumps(verdict, indent=1) + "\n")
    for k, v in checks.items():
        print(f"  {'PASS' if v else 'FAIL'}  {k}")
    print(f"-> {vf}   ok={verdict['ok']}")

    # Leave the tree in its baseline state: a later run must not silently read
    # this tool's leftovers.
    sh([PY, HERE / "vfs_overlay.py", "--clear", "--overlay", OVERLAY])
    raise SystemExit(0 if verdict["ok"] else 1)


if __name__ == "__main__":
    main()
