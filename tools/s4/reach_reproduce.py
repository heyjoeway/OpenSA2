#!/usr/bin/env python3
"""Reproduce the recorded run reach from a clean checkout of tracked files.

What this proves, and what it deliberately does not
---------------------------------------------------
On 2026-09-21 the driven run fell from 369 WinMain iterations to 1 because a
generated build input -- the edge table -- silently lost a reviewed dispatch
exception that had never existed anywhere in the tree. Every gate passed.
`check_run_reach.py` now fails a push whose inputs have moved, which is cheap
and catches that class in the commit that causes it.

This is the other half: it takes **only the tracked files**, at HEAD, into a
fresh directory, regenerates every build input the tree is supposed to
determine, rebuilds, runs, and compares the reach against the receipt. If the
tree really determines the build, the number comes back. If something the
build needs still lives only in generated state, it does not, and the gap is
named rather than assumed absent.

**Two inputs are reused from `private/` rather than reproduced, and the
boundary is the point of the exercise:**

* the shipped executable, which is the user's own file and can never be in the
  tree (design 12.9);
* the whole-image lift output -- 18 394 objects, hours of remill -- which is
  derived from that executable by a pipeline the emitter-determinism gate
  already re-derives and checks six functions of, digest for digest.

Everything else is regenerated from the clean checkout: the edge table from
`Documentation/dispatch-exceptions.json`, the extras registry checked against
`Documentation/lifted-extras.json`, the runtime, the harness and the link. The
2026-09-21 defect is inside that boundary, so this run would have caught it.

Where it runs
-------------
At stage gates, not on every push: it is a full relink and a driven run. G4's
exit criteria name it.

What the receipt ties the result to
-----------------------------------
The export's digest (`export_digest`): sha256 over every exported file's path
and content, in `export_listing` order, **less the receipt this tool writes**.
The receipt is the run's output, not its input, and until 2026-09-26 the
digest held its bytes as they stood before the write, so a second `--write`
before a commit made an export no later tree could match.
`Documentation/scripts/check_reach_reproduction.py --current` recomputes the
digest over the tree being gated, and accepts the receipt on HEAD's parent
only when HEAD is the commit that wrote it and the digests agree.

So nothing may move between the export and the commit, and `ship.py` moves one
file: it regenerates METRICS.md before it commits. `--write` therefore refuses
unless `metrics.py --check` passes first (2026-09-26: the receipt development_snapshot
committed was exported with METRICS.md at its parent's bytes, and its digest
matches no commit). The order at a gate is: every edit, including the gate
journal's note that this receipt is rewritten; `metrics.py --write`; this
tool with `--write`; `ship.py`.

Nothing may write a tracked file between the export and the commit, and that
includes `Documentation/agent-units.json`, which `tools/units.py` writes when a
unit starts or ends.

The order is enforced, not only written down: on every push,
`check_reach_reproduction.py` holds a receipt that the commit under
construction rewrites to the whole `--current` test (it names HEAD, and the
digest is the working tree's), so a receipt written out of order refuses the
push rather than the gate review a stage later. Through
`Documentation/scripts/ship_subset.py`, which copies the PATHs, runs each
`--run` in order and only then runs `metrics.py --write`, list every edited
file as a PATH and pass `--run ".venv/bin/python Documentation/scripts/metrics.py
--write"` *before* `--run ".venv/bin/python tools/s4/reach_reproduce.py --write"`,
no `--run` after it, and `--generated
Documentation/receipts/s4-reach-reproduction-v1.json`.

Usage
-----

    source tools/env.sh
    .venv/bin/python tools/s4/reach_reproduce.py [--keep] [--json PATH]

Prints summary lines prefixed `REPRODUCE`. Exit 0 only when the reach matches
the receipt exactly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "s3" / "whole_image"))
import common as C  # noqa: E402

RECEIPT = C.ROOT / "Documentation" / "receipts" / "s4-run-reach-v1.json"
OUT_REL = "Documentation/receipts/s4-reach-reproduction-v1.json"
OUT = C.ROOT / OUT_REL
TAG = "next+allarms"
# What the export digest leaves out: the receipt this tool writes, and nothing
# else. The receipt records this list, and the check refuses any other.
DIGEST_EXCLUDES = (OUT_REL,)


def sh(cmd: list[str], cwd: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                          timeout=7200, env=e)


def export_listing(root: Path) -> list[str]:
    """The files the tree would contain, not the files already committed:
    `git ls-files` plus everything untracked that .gitignore does not exclude,
    precisely the set a `git add -A` would produce, sorted."""
    listed = []
    for extra in ((), ("--others", "--exclude-standard")):
        done = subprocess.run(["git", "ls-files", "-z", *extra], cwd=str(root),
                              capture_output=True, text=True)
        if done.returncode:
            raise SystemExit(f"git ls-files failed in {root}: {done.stderr.strip()}")
        listed += done.stdout.split("\0")
    return sorted({f for f in listed if f})


def export_digest(root: Path, files: list[str], exclude: tuple[str, ...] = DIGEST_EXCLUDES,
                  substitute: dict[str, bytes | None] | None = None) -> str:
    """sha256 over (path, sha256(content)) for each of `files` that is a file under
    `root`, less `exclude`. `substitute` gives a path's bytes in place of the
    file's (None: the path was absent), which is how a receipt written before
    DIGEST_EXCLUDES existed is read back: its digest held the receipt's own bytes
    as they stood before the write."""
    h = hashlib.sha256()
    for rel in files:
        if rel in exclude:
            continue
        if substitute is not None and rel in substitute:
            data = substitute[rel]
            if data is None:
                continue
        else:
            src = root / rel
            if not src.is_file():
                continue
            data = src.read_bytes()
        h.update(rel.encode() + b"\0" + hashlib.sha256(data).digest())
    return h.hexdigest()


def metrics_refusal(root: Path, python: str) -> str | None:
    """Why `--write` must not run now, or None. `ship.py` regenerates METRICS.md
    between this export and the commit, so a METRICS.md that is not already
    what `metrics.py` generates would make the committed tree one the
    reproduction did not export."""
    done = subprocess.run([python, str(root / "Documentation" / "scripts" / "metrics.py"),
                           "--check"], cwd=str(root), capture_output=True, text=True)
    if done.returncode == 0:
        return None
    tail = (done.stderr.strip() or done.stdout.strip()).splitlines()
    return ("METRICS.md is not what metrics.py generates for this tree"
            + (f" ({tail[-1][:200]})" if tail else "") + "; ship.py would rewrite it after "
            "the export, and the committed tree would not be the one reproduced. Run "
            "`metrics.py --write` first, then this tool with --write, then ship.py")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="do not delete the checkout")
    ap.add_argument("--json", default=None)
    ap.add_argument("--write", action="store_true",
                    help="record the result in Documentation/receipts/"
                         "s4-reach-reproduction-v1.json, which "
                         "Documentation/scripts/check_reach_reproduction.py reads")
    a = ap.parse_args()

    if not RECEIPT.is_file():
        print(f"REPRODUCE_FAILED {RECEIPT.name} does not exist; there is no recorded "
              "reach to reproduce")
        return 1
    want = json.loads(RECEIPT.read_text())["reach"]
    py = str(C.ROOT / ".venv" / "bin" / "python")
    if a.write:
        why = metrics_refusal(C.ROOT, py)
        if why:
            print(f"REPRODUCE_FAILED --write refused: {why}")
            return 1

    # **The files the tree would contain, not the files already committed.**
    # Cloning HEAD was the first design and it cannot run before the commit
    # that introduces a change -- which is exactly when the question "does the
    # tree determine the build?" is worth asking. So the export is
    # `export_listing`: precisely the set a `git add -A` would produce.
    # Whether that set is committed yet is recorded rather than required,
    # because a reproduction of an uncommitted state is still a reproduction,
    # as long as it says so.
    files = export_listing(C.ROOT)
    if not files:
        print("REPRODUCE_FAILED git listed no files to export")
        return 1
    dirty = bool(sh(["git", "status", "--porcelain"], C.ROOT).stdout.strip())

    tmp = Path(tempfile.mkdtemp(prefix="sa2-reach-"))
    work = tmp / "repo"
    try:
        head = sh(["git", "rev-parse", "HEAD"], C.ROOT).stdout.strip()
        for rel in files:
            src = C.ROOT / rel
            if not src.is_file():
                continue
            dst = work / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())
        # Over the exported copies, before anything is linked beside them: the
        # digest is of exactly what was exported.
        digest = export_digest(work, files)
        print(f"REPRODUCE export={work} files={len(files)} head={head[:12]} "
              f"uncommitted={'yes' if dirty else 'no'} digest={digest[:12]}")

        # The two inputs the tree cannot contain. Symlinked, not copied: the
        # point is that the *tracked* files determine the build, and pretending
        # to reproduce the user's executable would be a lie either way.
        (work / "private").symlink_to(C.ROOT / "private")
        for name in (".venv", "tools/env.sh"):
            src, dst = C.ROOT / name, work / name
            if src.exists() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.symlink_to(src)

        # 1. The edge table, from the tracked exceptions in the clean checkout.
        r = sh([py, "tools/s4/mb_edges.py", "--tag", TAG, "--lift-tag", "next",
                "--arms-tag", "next", "--allow-from",
                f"private/generated/s4/link/extra_functions.{TAG}.json"], work)
        if r.returncode:
            print(f"REPRODUCE_FAILED mb_edges: {r.stderr[-1200:]}")
            return 1
        print(f"REPRODUCE edges: {r.stdout.strip().splitlines()[0]}")

        # 2. The extras registry must match what the clean checkout tracks.
        r = sh([py, "Documentation/scripts/check_lifted_extras.py"], work)
        print(f"REPRODUCE extras: {r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-200:]}")
        if r.returncode:
            return 1

        # 3. Rebuild and run exactly as the clean checkout's own
        #    `reach_receipt.run()` does: its environment, tag, driver and probe
        #    arguments. This step had its own copy of the environment until
        #    2026-09-22, and the copy kept the 400 000 import budget after the
        #    receipt's moved to 4 000 000, so it reproduced a different run and
        #    failed. One definition, read from the tree being tested. The device
        #    profile is the receipt's too: a reach measured on a capture's
        #    devices and reproduced on the default would be another run, and
        #    R.run refuses a run that did not read the profile it was handed.
        r = sh([py, "-c", "import json, sys; sys.path[:0] = ['tools/s4', 'tools/s3/whole_image']; "
                "import reach_receipt as R; print('REACH_JSON ' + json.dumps(R.run('reproduce', "
                "device_profile=R.recorded_device_profile())))"],
               work)
        line = [x for x in (r.stdout or "").splitlines() if x.startswith("REACH_JSON ")]
        if r.returncode or not line:
            print(f"REPRODUCE_FAILED the clean checkout's reach run did not complete\n"
                  f"{(r.stdout or '')[-1500:]}\n{(r.stderr or '')[-1500:]}")
            return 1
        full = json.loads(line[-1][len("REACH_JSON "):])
        got = {k: full[k] for k in ("iterations", "import_calls", "frames_presented")}

        bad = {k: (want.get(k), got[k]) for k in got if want.get(k) != got[k]}
        print(f"REPRODUCE reach iterations={got['iterations']} "
              f"import_calls={got['import_calls']} frames={got['frames_presented']}")
        result = {"head": head, "uncommitted": dirty,
                  "export_digest": digest, "digest_excludes": list(DIGEST_EXCLUDES),
                  "exported_files": len(files),
                  "recorded": {k: want.get(k) for k in got},
                  "reproduced": got, "match": not bad,
                  "device_profile": full.get("device_profile"),
                  "guest_paths": full.get("guest_paths")}
        if a.json:
            Path(a.json).write_text(json.dumps(result, indent=2) + "\n")
        if a.write:
            import importlib
            sys.path.insert(0, str(HERE))
            manifest = importlib.import_module("reach_receipt").manifest()
            OUT.write_text(json.dumps({
                "schema": "sa2-recomp-receipt/v1",
                "id": "s4-reach-reproduction-v1",
                "question": "does a clean checkout of the tracked tree reproduce "
                            "the recorded run reach?",
                "generated_by": "tools/s4/reach_reproduce.py",
                "commit": head,
                "inputs": {"pipeline": manifest["pipeline"]},
                "manifest": manifest,
                "result": result,
            }, indent=2, sort_keys=True) + "\n")
            print(f"REPRODUCE wrote {OUT.relative_to(C.ROOT)}")
        if bad:
            for k, (w, g) in bad.items():
                print(f"  {k}: the receipt records {w} and a clean checkout produces {g}")
            print(f"REPRODUCE_FAILED mismatched={len(bad)}")
            return 1
        print("REPRODUCE_OK the tracked tree reproduces the recorded reach")
        return 0
    finally:
        if a.keep:
            print(f"REPRODUCE kept {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
