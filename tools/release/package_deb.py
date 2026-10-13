#!/usr/bin/env python3
"""Package an existing OpenSA2 Linux release tree as a Debian amd64 package.

The input is read only. The stage is a fresh sibling directory beside --output.
Use --deny-file with a private JSON {"terms": [...]} list during anonymous releases.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import struct
import sys
import time

VERSION = "0.1.0~alpha1"
LABEL = "v0.1.0-alpha.1"
REQUIRED = ("bin/sa2-game", "bin/sa2recomp", "bin/sa2-install", "bin/sa2_player", "bin/sa2_player_gpu",
            "lib/libSDL3.so.0", "lib/libdxvk_d3d9.so.0", "lib/libpng16.so.16", "lib/libzstd.so.1",
            "share/game.env", "share/dxvk.conf", "release.manifest")
README = """OpenSA2 {label}

OpenSA2 is an experimental native port of the 2012 PC release of Sonic Adventure 2.
You must supply your own unmodified game installation. Game assets are not included.

Start OpenSA2 from your application menu, or run opensa2 in a terminal.
The first launch finds the game in Steam and verifies its files. If needed, it asks for
its folder or a ZIP of that folder. The original installation is read only.

This amd64 package requires Ubuntu 24.04, Debian 13, or a newer compatible distribution,
plus a working Vulkan driver. It does not support Ubuntu 22.04 or Debian 12.

Keyboard and SDL-compatible gamepads are supported. Connect a gamepad before launch,
or connect one while playing. Closing the game window ends the session.

Saves, settings and session logs use your per-user directories:
  ~/.local/share/sa2recomp
  ~/.config/sa2recomp
XDG_DATA_HOME and XDG_CONFIG_HOME override those locations. Package installation,
upgrade and removal do not copy game assets or delete these per-user files.

This is an early experimental build. Full stage and event coverage is unverified.
A known CRT dispatch trap can end a session after a stage event. Back up saves.
The package contains the PC double-f80 build. Full parity remains unverified.

Source and issues: https://github.com/kittycache9/OpenSA2
Project code: GPL-3.0-only. Components retain their notices in licenses/.
"""


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def command(*args: str, ok: tuple[int, ...] = (0,)) -> str:
    r = subprocess.run(args, text=True, capture_output=True)
    if r.returncode not in ok:
        raise RuntimeError(f"{args[0]} failed ({r.returncode}): {r.stdout}{r.stderr}")
    return r.stdout + r.stderr


def verify_input(release: Path) -> dict:
    for name in REQUIRED:
        if not (release / name).is_file():
            raise ValueError(f"release lacks {name}")
    if any(p.is_symlink() for p in release.rglob("*")):
        raise ValueError("release symlinks must be resolved before packaging")
    lines = (release / "release.manifest").read_text().splitlines()
    meta = json.loads(lines[0][2:])
    if meta.get("target") != "x86_64-linux":
        raise ValueError("input target is not x86_64-linux")
    for l in lines[1:]:
        if not l:
            continue
        digest, size, name = l.split("  ", 2)
        p = release / name
        if p.stat().st_size != int(size) or sha(p) != digest:
            raise ValueError(f"input release manifest mismatch: {name}")
    return meta


def elf_sections(blob: bytes) -> dict[str, tuple[int, int, int, int, int, int]]:
    if blob[:6] != b"\x7fELF\x02\x01":
        raise ValueError("only little-endian ELF64 is supported")
    shoff = struct.unpack_from("<Q", blob, 40)[0]
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", blob, 58)
    headers = [struct.unpack_from("<IIQQQQIIQQ", blob, shoff + i * shentsize) for i in range(shnum)]
    names_header = headers[shstrndx]
    names = blob[names_header[4]:names_header[4] + names_header[5]]
    sections = {}
    for h in headers:
        name = names[h[0]:].split(b"\x00", 1)[0].decode()
        sections[name] = (h[4], h[5], h[2], h[3], h[8], h[1])
    return sections


def redact_blob_diagnostics(before: bytes, prefix: str) -> tuple[bytes, int]:
    sections = elf_sections(before)
    offset, size, flags = sections[".rodata"][:3]
    if flags & 4:
        raise ValueError(".rodata unexpectedly has executable flags")
    old = prefix.encode()
    replacement = b"/build/opensa2/"
    if not old.endswith(b"/") or len(replacement) > len(old):
        raise ValueError("diagnostic source prefix must end with / and fit the public path")
    replacement += b"/" * (len(old) - len(replacement))
    rodata = before[offset:offset + size]
    matches = [m for m in re.finditer(rb"[ -~]{5,}(?=\x00)", rodata) if old in m.group()]
    suffixes = (b".h", b".cpp", b".c")
    for match in matches:
        if not match.group().startswith(old) or not match.group().endswith(suffixes):
            raise ValueError("a private path is not a complete diagnostic source filename")
    after = bytearray(before)
    for match in matches:
        start = offset + match.start()
        after[start:start + len(old)] = replacement
    return bytes(after), len(matches)


def allocated_sections_verified(before: bytes, after: bytes, prefix: str | None) -> bool:
    """Hold allocated bytes and section layout fixed except exact diagnostic string ranges."""
    expected = redact_blob_diagnostics(before, prefix)[0] if prefix else before
    old_sections, new_sections = elf_sections(before), elf_sections(after)
    for name, layout in old_sections.items():
        offset, size, flags, address, alignment, section_type = layout
        if flags & 2:
            if new_sections[name] != layout:
                raise ValueError(f"allocated section layout changed: {name}")
            if section_type != 8 and expected[offset:offset + size] != after[offset:offset + size]:
                raise ValueError(f"allocated section bytes changed outside diagnostic ranges: {name}")
    old_alloc = {n for n, h in old_sections.items() if h[2] & 2}
    new_alloc = {n for n, h in new_sections.items() if h[2] & 2}
    if old_alloc != new_alloc:
        raise ValueError("allocated section set changed")
    return True


def redact_diagnostic_paths(path: Path, prefix: str) -> dict:
    before = path.read_bytes()
    after, count = redact_blob_diagnostics(before, prefix)
    allocated_sections_verified(before, after, prefix)
    if count:
        path.write_bytes(after)
    return {"diagnostic_paths_redacted": count, "allocated_sections_verified": True,
            "before_sha256": hashlib.sha256(before).hexdigest(),
            "after_sha256": hashlib.sha256(after).hexdigest()}


def scan_tree(tree: Path, terms: list[str]) -> None:
    for p in sorted(tree.rglob("*")):
        if not p.is_file():
            continue
        b = p.read_bytes().lower()
        hits = [term for term in terms if term.lower().encode() in b]
        if hits:
            raise ValueError(f"private identifier in {p.relative_to(tree)}: {hits}")


def text(path: Path, value: str, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)
    path.chmod(0o755 if executable else 0o644)


def build(release: Path, source: Path, output: Path, version: str, label: str,
          deny_file: Path | None = None, diagnostic_prefix: str | None = None) -> Path:
    if not re.fullmatch(r"[0-9][A-Za-z0-9.+~:-]*", version):
        raise ValueError("invalid Debian version")
    release, source, output = release.resolve(), source.resolve(), output.resolve()
    out_dir = output.parent
    if out_dir == release or release in out_dir.parents or out_dir in release.parents:
        raise ValueError("output must be separate from input release")
    meta = verify_input(release)
    out_dir.mkdir(parents=True, exist_ok=True)
    stage = out_dir / f"stage-{version}"
    if stage.exists() or output.exists():
        raise ValueError("stage or output already exists; use a new output directory")
    payload = stage / "usr/lib/opensa2"
    shutil.copytree(release, payload)
    # Remove private build paths carried only by ELF debugging/symbol sections.
    # The player verifies SDL and DXVK by full-file hash, so preserve bundled libraries.
    for p in sorted((payload / "bin").iterdir()):
        command("strip", "--strip-unneeded", str(p))
        sections = command("readelf", "-S", str(p))
        if ".debug_" in sections:
            raise ValueError(f"debug sections remain in {p.name}")
    transforms = {}
    if diagnostic_prefix:
        for p in sorted((payload / "bin").iterdir()) + sorted((payload / "lib").iterdir()):
            transforms[p.relative_to(payload).as_posix()] = redact_diagnostic_paths(p, diagnostic_prefix)
    for p in sorted((payload / "bin").iterdir()) + sorted((payload / "lib").iterdir()):
        allocated_sections_verified((release / p.relative_to(payload)).read_bytes(), p.read_bytes(), diagnostic_prefix)
    for p in sorted((payload / "lib").iterdir()):
        if (release / p.relative_to(payload)).read_bytes() != p.read_bytes():
            raise ValueError(f"bundled library changed: {p.name}")
    # The old metadata names pre-strip hashes. Keep its digest as the input identity,
    # and write package metadata against the exact files that the player receives.
    (payload / "release.manifest").unlink()
    (payload / "README.txt").unlink()
    text(payload / "README.txt", README.format(label=label))
    for name in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"):
        shutil.copyfile(source / name, payload / name)
    for p in sorted((source / "licenses").glob("*")):
        if p.is_file():
            (payload / "licenses").mkdir(exist_ok=True)
            shutil.copyfile(p, payload / "licenses" / p.name)
    package_meta = {
        "schema": "opensa2-binary-package/v1", "version": label, "target": "x86_64-linux",
        "input_manifest_sha256": sha(release / "release.manifest"),
        "input_built_at": meta.get("written"),
        "f80_mode": ((meta.get("game") or {}).get("f80") or {}).get("mode", "native-x87"),
        "sources": meta.get("sources", {}),
        "host_libraries": meta.get("host_libraries", []),
    }
    manifest_lines = ["# " + json.dumps(package_meta, sort_keys=True)]
    for p in sorted(payload.rglob("*")):
        if p.is_file():
            manifest_lines.append(f"{sha(p)}  {p.stat().st_size}  {p.relative_to(payload).as_posix()}")
    text(payload / "release.manifest", "\n".join(manifest_lines) + "\n")
    text(stage / "usr/bin/opensa2", '#!/bin/sh\nexec /usr/lib/opensa2/bin/sa2recomp "$@"\n', True)
    text(stage / "usr/bin/opensa2-install", '#!/bin/sh\nexec /usr/lib/opensa2/bin/sa2-install "$@"\n', True)
    desktop = stage / "usr/share/applications/opensa2.desktop"
    text(desktop, """[Desktop Entry]
Type=Application
Name=OpenSA2
GenericName=Sonic Adventure 2 native port
Comment=Play Sonic Adventure 2 with your own game files
Exec=opensa2
TryExec=opensa2
Icon=applications-games
Terminal=false
Categories=Game;ActionGame;
Keywords=Sonic;Adventure;SA2;
""")
    command("desktop-file-validate", str(desktop))
    doc = stage / "usr/share/doc/opensa2"
    text(doc / "README", README.format(label=label))
    text(doc / "copyright", """Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: OpenSA2
Upstream-Contact: https://github.com/kittycache9/OpenSA2/issues
Source: https://github.com/kittycache9/OpenSA2

Files: *
Copyright: 2026 kittycache
License: GPL-3.0-only
 The project license is installed at /usr/lib/opensa2/LICENSE.
 Third-party code and data keep their own terms. The notices are installed at
 /usr/lib/opensa2/THIRD_PARTY_NOTICES.md and /usr/lib/opensa2/licenses/. Bundled Linux distribution notices remain in
 /usr/lib/opensa2/share/licenses/.
 This notice does not grant rights to Sonic Adventure 2 or its game assets.
""")
    installed_size = (sum(p.stat().st_size for p in stage.rglob("*") if p.is_file()) + 1023) // 1024
    text(stage / "DEBIAN/control", f"""Package: opensa2
Version: {version}
Section: games
Priority: optional
Architecture: amd64
Maintainer: kittycache <341725219+kittycache9@users.noreply.github.com>
Installed-Size: {installed_size}
Depends: libc6 (>= 2.38), libstdc++6 (>= 13.2), libgcc-s1, zlib1g, libvulkan1
Recommends: mesa-vulkan-drivers, zenity
Homepage: https://github.com/kittycache9/OpenSA2
Description: Experimental native port of Sonic Adventure 2
 OpenSA2 runs the 2012 PC game's recompiled logic with a native runtime.
 You must supply your own unmodified Sonic Adventure 2 installation.
 Game assets are not included. This early build has known coverage gaps.
""")
    terms = []
    if deny_file:
        terms = json.loads(deny_file.read_text())["terms"]
    scan_tree(stage, terms)
    # Stable archive ordering/time allows a rerun with identical inputs to reproduce bytes.
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "1791828000"))
    for p in sorted(stage.rglob("*"), reverse=True):
        os.utime(p, (epoch, epoch), follow_symlinks=False)
    os.utime(stage, (epoch, epoch))
    cmd_env = dict(os.environ, SOURCE_DATE_EPOCH=str(epoch))
    subprocess.run(["dpkg-deb", "--root-owner-group", "--threads-max=1", "-Zxz", "-z6",
                    "--build", str(stage), str(output)], check=True, env=cmd_env)
    details = command("dpkg-deb", "--info", str(output))
    contents = command("dpkg-deb", "--contents", str(output))
    if "root/root" not in contents or "Package: opensa2" not in details:
        raise ValueError("Debian package metadata verification failed")
    receipt = {"artifact": output.name, "sha256": sha(output), "bytes": output.stat().st_size,
               "version": version, "input_manifest_sha256": sha(release / "release.manifest"),
               "input_game_sha256": sha(release / "bin/sa2-game"),
               "package_game_sha256": sha(payload / "bin/sa2-game"),
               "checks": ["input_manifest", "debug_sections_removed", "privacy_scan",
                          "desktop_file", "debian_metadata", "root_ownership", "allocated_sections_layout_and_bytes_verified", "bundled_libraries_unchanged"],
               "privacy_transforms": transforms, "game_smoke": "not_run"}
    text(out_dir / "packaging-receipt.json", json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))
    return output


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--release", type=Path, required=True)
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--label", default=LABEL)
    ap.add_argument("--deny-file", type=Path)
    ap.add_argument("--diagnostic-prefix", help="private source-root prefix to redact from copied ELF diagnostics")
    a = ap.parse_args()
    build(a.release, a.source_root, a.output, a.version, a.label, a.deny_file, a.diagnostic_prefix)
    return 0


if __name__ == "__main__":
    sys.exit(main())
