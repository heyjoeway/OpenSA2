#!/usr/bin/env python3
"""Break the file system on purpose and check that the probes notice.

Subtitle: three exact source substitutions in `runtime/`, one rebuild, one
guest run, then the sources are put back -- and the failing JSON is kept.

Why this exists
---------------
Five checks in this project have turned out unable to fail, and the last two
were found by deliberately breaking a stub and watching the probe still print
`"ok": true`. A new probe therefore does not get believed because it passes.
It gets believed after its assertions have been seen to fail, one at a time,
against a runtime that is wrong in exactly the way each assertion is about.

Each break targets one assertion and nothing else, so the JSON shows which
assertion moved and which did not:

  escape_clamp       `..` past the root is clamped instead of refused, which is
                     the classic path-traversal bug. The path then resolves
                     inside the installation and is served as an ordinary miss.
                     -> `escape_refused_and_control_served` must go false.
  seek_end_constant  SetFilePointer(FILE_END) returns a fixed number instead of
                     the file's length. -> `guest_sized_its_files` must go false.
  short_read         ReadFile returns nine tenths of what was asked for, so the
                     guest parses a truncated file while the probe reads a whole
                     one. -> `guest_parsed_its_config` must go false.

The sources are restored in a `finally`, and the tool prints the digest of each
file before and after so a run that failed to restore is visible rather than
silent.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/fs_break.py --name fs.break
"""
from __future__ import annotations

import argparse
import hashlib
import json
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

# (file, exact text to replace, replacement, which assertion it targets)
BREAKS = [
    ("runtime/sa2_files.c",
     """        if (len == 2 && s[0] == '.' && s[1] == '.') {
            if (n == 0) return SA2_VFS_ESCAPE;
            n--;
            continue;
        }""",
     """        if (len == 2 && s[0] == '.' && s[1] == '.') {
            if (n == 0) continue;   /* BREAK escape_clamp: clamp instead of refuse */
            n--;
            continue;
        }""",
     "escape_refused_and_control_served"),
    ("runtime/sa2_imports.c",
     """    if (a[2]) gpoke32(a[2], (uint32_t)(pos >> 32));
    note_out((uint32_t)pos, (uint32_t)(pos >> 32), 2);
    g_last_error = SA2_ERROR_SUCCESS;
    return (uint32_t)pos;""",
     """    if (a[2]) gpoke32(a[2], (uint32_t)(pos >> 32));
    if (a[3] == 2u) pos = 0x1000u;   /* BREAK seek_end_constant */
    note_out((uint32_t)pos, (uint32_t)(pos >> 32), 2);
    g_last_error = SA2_ERROR_SUCCESS;
    return (uint32_t)pos;""",
     "guest_sized_its_files"),
    # The first version of this break capped the *requested* length, which does
    # nothing: every read on this path asks for more than the file holds, so
    # nine tenths of 4096 is still all 156 bytes. It has to cap what is
    # *reported*, and it has to do it in the import stub rather than in
    # `sa2_vfs_read`, because the probe's own host-side read goes through
    # `sa2_vfs_read` too -- breaking both sides identically is how a comparison
    # goes on agreeing while being wrong.
    ("runtime/sa2_imports.c",
     """    if (a[3]) gpoke32(a[3], got);
    note_out(got, 0, 1);
    g_last_error = SA2_ERROR_SUCCESS;
    return 1;""",
     """    got -= got / 10u;                /* BREAK short_read: the guest only */
    if (a[3]) gpoke32(a[3], got);
    note_out(got, 0, 1);
    g_last_error = SA2_ERROR_SUCCESS;
    return 1;""",
     "guest_parsed_its_config"),
]


class _Skip(Exception):
    """--from-run: reuse the kept run instead of rebuilding a broken one."""


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def sh(cmd):
    return subprocess.run([str(c) for c in cmd], cwd=str(C.ROOT), capture_output=True,
                          text=True, timeout=3600)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="fs.break")
    ap.add_argument("--install", default="private/install")
    ap.add_argument("--from-run", action="store_true",
                    help="recompute the verdict from the kept run JSON, without rebuilding")
    args = ap.parse_args()
    RUNDIR.mkdir(parents=True, exist_ok=True)

    files = sorted({b[0] for b in BREAKS})
    originals = {f: (C.ROOT / f).read_text() for f in files}
    before = {f: sha(C.ROOT / f) for f in files}
    applied = []
    out = RUNDIR / f"{args.name}.json"
    result = {"written": time.strftime("%Y-%m-%dT%H:%M:%S"), "targets": [b[3] for b in BREAKS],
              "sha256_before": before}
    if args.from_run:
        prev = RUNDIR / f"{args.name}.verdict.json"
        if prev.is_file():
            old = json.loads(prev.read_text())
            for k in ("sha256_broken", "link_rc", "link_tail", "vfs_check_rc",
                      "vfs_check_tail", "sha256_after_restore", "restored"):
                if k in old:
                    result[k] = old[k]
        result["recomputed_from"] = str((RUNDIR / f"{args.name}.json").relative_to(C.ROOT))
    try:
        if args.from_run:
            raise _Skip()
        text = dict(originals)
        for path, old, new, target in BREAKS:
            if text[path].count(old) != 1:
                raise SystemExit(f"{path}: the text this break replaces occurs "
                                 f"{text[path].count(old)} times, not once; the break would "
                                 f"not be the one it says it is")
            text[path] = text[path].replace(old, new)
            applied.append(target)
        for f in files:
            (C.ROOT / f).write_text(text[f])
        result["sha256_broken"] = {f: sha(C.ROOT / f) for f in files}

        sh([PY, HERE / "vfs_overlay.py", "--clear", "--overlay", OVERLAY])
        # The same extra objects and generated edge table `crt_run.py` links.
        # Without them the guest dies in CRT startup at 0x007a7e9e and the run
        # says nothing about the file system -- which is what the first version
        # of this tool measured.
        link_dir = C.GEN / "s4" / "link"
        extra = ["--extra-c", link_dir / "mb_edges.next+allarms.c"]
        state_f = link_dir / "extra_functions.next+allarms.json"
        if state_f.is_file():
            objs = json.loads(state_f.read_text()).get("objects", [])
            if objs:
                extra += ["--extra-c", link_dir / "extra_functions.next+allarms.c"]
                for o in objs:
                    extra += ["--extra-o", o]
        p = sh([PY, HERE / "link_s4.py", "--tag", "next+allarms",
                "--main", "harness/crt_probe.c", "--name", args.name, *extra,
                "--run", C.EXE, "--run=--json", "--run", out,
                "--run=--require-files", "--run=--require-stop",
                "--run=--stop-import", "--run", "LoadIconW",
                "--env", f"SA2_INSTALL={args.install}",
                "--env", f"SA2_VFS_WRITE_ROOT={OVERLAY}"])
        result["link_rc"] = p.returncode
        result["link_tail"] = (p.stdout or "")[-1500:]
        # The unit probe sees the first break without any of the slow machinery.
        v = sh([PY, HERE / "vfs_check.py", "--out-dir", str(C.GEN / "s4" / "vfs-break")])
        result["vfs_check_rc"] = v.returncode
        result["vfs_check_tail"] = (v.stdout or "")[-1200:]
    except _Skip:
        pass
    finally:
        if not args.from_run:
            for f in files:
                (C.ROOT / f).write_text(originals[f])
            result["sha256_after_restore"] = {f: sha(C.ROOT / f) for f in files}
            result["restored"] = result["sha256_after_restore"] == before

    doc = json.loads(out.read_text()) if out.is_file() else {}
    a = doc.get("assert", {})
    result["run_json"] = str(out.relative_to(C.ROOT)) if out.is_file() else None
    result["run_ok"] = doc.get("ok")
    result["assert"] = a
    result["targets_failed"] = {t: (a.get(t) is False) for t in result["targets"]}
    # The break must move exactly what it aimed at. Untouched assertions staying
    # true is half the evidence: a break that turned everything false would
    # prove only that the probe can fail, not that these checks are the ones
    # measuring these facts.
    # `guest_read_a_real_file` conjoins two of the targets, so it is expected to
    # move with them: it is a derived verdict, not an independent one.
    # Two kinds of assertion are expected to move with the breaks and are not
    # evidence of an untargeted one:
    #   * `guest_read_a_real_file` conjoins two of the targets;
    #   * `ended_where_intended` / `reached_the_stop_address` move because the
    #     truncated config sends the guest down its own configuration-failure
    #     path, which faults at guest 0x00000004 before the deliberate stop.
    #     That is the game's code doing what it does with a half-read config,
    #     and it is the same address the empty-installation column reaches.
    # What must stay true is everything the CRT slice established, which is how
    # this says the breaks were surgical rather than merely destructive.
    collateral = {"guest_read_a_real_file", "ended_where_intended",
                  "reached_the_stop_address"}
    result["expected_collateral"] = sorted(collateral)
    untouched = {k: v for k, v in a.items()
                 if k not in result["targets"] and k not in collateral}
    result["untouched_assertions_still_true"] = bool(untouched) and all(untouched.values())
    result["ok"] = (result["restored"] and doc.get("ok") is False
                    and all(result["targets_failed"].values())
                    and result["untouched_assertions_still_true"]
                    and result["vfs_check_rc"] != 0)
    bf = RUNDIR / f"{args.name}.verdict.json"
    bf.write_text(json.dumps(result, indent=1) + "\n")
    for t, failed in result["targets_failed"].items():
        print(f"  {'FAILED as required' if failed else 'STILL PASSED (a hole)'}  {t}")
    print(f"restored={result['restored']} run_ok={result['run_ok']} "
          f"vfs_check_rc={result['vfs_check_rc']} "
          f"untouched_still_true={result['untouched_assertions_still_true']}")
    print(f"-> {bf}   ok={result['ok']}")
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
