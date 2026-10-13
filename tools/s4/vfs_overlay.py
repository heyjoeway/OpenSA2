#!/usr/bin/env python3
"""Put a changed file in the guest's write overlay, so the guest reads it.

Subtitle: copy one file out of the installation into
`private/generated/s4/vfsroot`, change named attribute values in it, and leave
the installation alone.

Why this exists
---------------
The configuration oracle in `harness/crt_probe.c` compares what the guest
parsed against what the file says. That comparison is only worth something if
*both sides move*: a probe whose expected value is the installation's own
1280x720 cannot tell a guest that read the file from a guest that was handed a
constant. Changing the file is how the two are told apart -- and the
installation is read-only input, so the changed file goes in the overlay, which
is exactly what the overlay is for.

This is the same operation the runtime performs by itself when the guest opens
an installation file for writing (`copy_up` in `runtime/sa2_files.c`); it is
done here so a run can start from a chosen state instead of having to reach the
code that writes one.

Nothing it writes is tracked: the overlay lives under `private/generated/`,
which `.gitignore` excludes wholesale, and no byte of the installation goes
anywhere else.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/vfs_overlay.py --file "Config/UserConfig.cfg" \\
      --set Width=1024 --set Height=768 --set RefreshRate=75
  .venv/bin/python tools/s4/vfs_overlay.py --clear
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "s3" / "whole_image"))
import common as C  # noqa: E402

DEFAULT_OVERLAY = C.GEN / "s4" / "vfsroot"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--install", default=None, help="default $SA2_INSTALL or private/install")
    ap.add_argument("--overlay", default=None, help=f"default {DEFAULT_OVERLAY}")
    ap.add_argument("--file", default="Config/UserConfig.cfg",
                    help="the path relative to the installation root")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help='an XML attribute to rewrite, e.g. Width=1024')
    ap.add_argument("--clear", action="store_true", help="empty the overlay and exit")
    args = ap.parse_args()

    install = Path(args.install or os.environ.get("SA2_INSTALL")
                   or (C.ROOT / "private" / "install")).resolve()
    overlay = Path(args.overlay or os.environ.get("SA2_VFS_WRITE_ROOT") or DEFAULT_OVERLAY)

    if args.clear:
        if overlay.is_dir():
            shutil.rmtree(overlay)
        overlay.mkdir(parents=True, exist_ok=True)
        print(f"overlay cleared: {overlay}")
        return

    src = install / args.file
    if not src.is_file():
        raise SystemExit(f"not in the installation: {src}")
    dst = overlay / args.file
    dst.parent.mkdir(parents=True, exist_ok=True)

    text = src.read_text(encoding="utf-8", errors="strict")
    applied, missing = {}, []
    for kv in args.set:
        key, _, value = kv.partition("=")
        pat = re.compile(r'(\b' + re.escape(key) + r'=")([^"]*)(")')
        m = pat.search(text)
        if not m:
            missing.append(key)
            continue
        applied[key] = {"was": m.group(2), "now": value}
        text = pat.sub(lambda mm: mm.group(1) + value + mm.group(3), text, count=1)
    if missing:
        # Refusing rather than writing a file that silently lacks the change is
        # the point: a run that then agreed with the installation would look
        # like a pass.
        raise SystemExit(f"attributes not present in {args.file}: {missing}")
    dst.write_text(text, encoding="utf-8")

    state = {
        "written": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "install_root": str(install),
        "overlay_root": str(overlay),
        "file": args.file,
        "applied": applied,
        "install_bytes": src.stat().st_size,
        "overlay_bytes": dst.stat().st_size,
    }
    sf = overlay.parent / "vfsroot.state.json"
    sf.write_text(json.dumps(state, indent=1) + "\n")
    print(f"overlay {args.file}: " + ", ".join(f"{k} {v['was']} -> {v['now']}"
                                               for k, v in applied.items()))
    print(f"-> {dst}  (state: {sf})")


if __name__ == "__main__":
    main()
