#!/usr/bin/env python3
"""release_zip: the Windows prebuilt as one zip, for the user's native Windows run.

Subtitle: tools/s7/release_build.py --target x86_64-windows's directory, under windows-x86_64/,
with the routes runner (host/launch/windows_routes.py, as tools/windows_routes.py) and the
run's brief (Documentation/s7-windows-native-run.md, as README-native-run.md) beside it, the
way the user copies it to the Windows PC (`Documentation/s7-windows-native-run.md`). The zip
is written by rename, tested, and printed with its size, its sha256, the player's and the game's
sha256, and the game's f80 mode as the release manifest records it (`f80_mode`: release_build.py
builds the double mode by default, `Documentation/decisions/2026-10-12-S7-pc-double-default.md`;
native-x87 when the manifest's game names no f80 build). Releases are never committed (AGENTS.md
rule 9); the zip stays under private/.

  .venv/bin/python tools/s7/release_zip.py [--copy-to DIR/NAME.zip]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RELEASE = ROOT / "private" / "generated" / "s7" / "release" / "windows-x86_64"
OUT = ROOT / "private" / "generated" / "s7" / "release" / "sa2recomp-windows-x86_64.zip"
EXTRA = {"windows-x86_64/tools/windows_routes.py": ROOT / "host" / "launch" / "windows_routes.py",
         "windows-x86_64/tools/windows_play.py": ROOT / "host" / "launch" / "windows_play.py",
         "windows-x86_64/README-native-run.md": ROOT / "docs" / "windows-native-run.md"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def f80_mode(release: Path = RELEASE) -> str:
    """The release's game's f80 mode, from its manifest's head (release_build.py's notes)."""
    head = json.loads((release / "release.manifest").read_text().splitlines()[0][2:])
    return ((head.get("game") or {}).get("f80") or {}).get("mode") or "native-x87"


def pack(release: Path = RELEASE, out: Path = OUT) -> str:
    if not (release / "release.manifest").is_file():
        raise SystemExit(f"release_zip: {release} holds no release.manifest; run release_build.py")
    tmp = out.with_name(out.name + ".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as z:
        for p in sorted(x for x in release.rglob("*") if x.is_file()):
            z.write(p, "windows-x86_64/" + p.relative_to(release).as_posix())
        for name, src in EXTRA.items():
            z.write(src, name)
    with zipfile.ZipFile(tmp) as z:
        if z.testzip() is not None:
            raise SystemExit(f"release_zip: {tmp} does not read back")
    os.replace(tmp, out)
    return sha256(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--copy-to", type=Path, help="also copy the zip here (a share the PC reads)")
    args = ap.parse_args(argv)
    digest = pack()
    player = sha256(RELEASE / "bin" / "sa2_player_gpu.exe")
    game = sha256(RELEASE / "bin" / "sa2-game.exe")
    print(f"RELEASE_ZIP_OK {OUT} {OUT.stat().st_size} bytes sha256 {digest} player {player} "
          f"game {game} f80 {f80_mode()}")
    if args.copy_to:
        # Written in place, not renamed: the VM's Samba share never listed a file a local rename
        # made (2026-10-12); the PC reads the copy only after its sha256 is sent.
        shutil.copyfile(OUT, args.copy_to)
        if sha256(args.copy_to) != digest:
            raise SystemExit(f"release_zip: {args.copy_to} does not hash as {OUT}")
        print(f"copied to {args.copy_to}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
