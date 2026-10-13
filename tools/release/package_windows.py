#!/usr/bin/env python3
"""Package a tested Windows runtime as an OpenSA2 ZIP without changing its binaries.

Example:
  python3 tools/release/package_windows.py --release /path/to/windows-x86_64 \
    --source-root . --output /path/to/OpenSA2-v0.1.0-alpha.1-windows-x86_64.zip

Use --deny-file with a private JSON {"terms": [...]} during anonymous releases.
Use --notices to supply the shared binary third-party notice file.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import zipfile
from pathlib import Path

RUNTIME = (
    'bin/SDL3.dll', 'bin/sa2-game.exe', 'bin/sa2-install.exe',
    'bin/sa2_player_gpu.exe', 'bin/sa2recomp.exe', 'share/game.env',
)
LICENSES = (
    'SDL3.txt', 'SDL-HIDAPI-original.txt', 'DXVK.txt', 'dxbc-spirv.txt',
    'Remill-Apache-2.0.txt', 'libpng.txt', 'zlib.txt', 'zstd.txt', 'mingw-w64.txt',
    'LLVM-libcxx.txt', 'LLVM-libcxxabi.txt', 'LLVM-libunwind.txt',
    'Wine-LICENSE.txt', 'Wine-LGPL-2.1.txt', 'Proton-Wine-attribution.txt',
    'Unicode.txt', 'CLDR-release48.txt',
)

def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def readme(version: str) -> bytes:
    return f'''OpenSA2 {version} for Windows x86-64
======================================

An experimental native port of the 2012 PC release of Sonic Adventure 2.
You need your own unmodified supported PC installation. This download includes
recompiled game logic, but no original PC executable or game assets.

Start
  Extract the entire ZIP to a writable folder, then run OpenSA2.cmd.
  Keep bin and share beside OpenSA2.cmd.
  The first start finds your game through Steam and verifies its files. If Steam
  has no copy, select the folder containing sonic2app.exe, or a ZIP of that folder.
  The original game installation is read and is not changed.

Requirements
  Windows 10 or later, x86-64, and a Vulkan or Direct3D 12 graphics driver.

Controls
  Use the keyboard mapping from your game's settings, or connect a controller.
  Xbox, PlayStation, Switch Pro and other SDL-supported controllers are supported.

Saves, settings and logs
  These are stored separately under %LOCALAPPDATA%\\sa2recomp.

Experimental build
  The packaged runtime passed a native Windows smoke run through boot, title,
  menu, a stage, input, and closing the window. It has not passed all-stage or
  full-game testing. A known missing block at 0x00859AC8 can stop a later stage
  event. Other stages and events may expose further problems.
  This runtime uses the faster double floating-point mode.

Source, notices and bug reports
  https://github.com/kittycache9/OpenSA2
  See LICENSE, NOTICE and THIRD_PARTY_NOTICES.md. licenses contains the preserved
  component terms. release.manifest lists the packaged files with SHA-256 hashes.
  Remove personal paths and identifiers from diagnostics before posting them.
  Do not upload game files or recordings containing game data.

Closing the game window ends the session.
'''.encode()


def notices() -> bytes:
    return '''# OpenSA2 Windows binary notices

Original OpenSA2 project code is GPL-3.0-only. The project license does not grant
rights to Sonic Adventure 2, its executable, recompiled game logic, or its assets.
The user supplies their supported game installation. This package includes
recompiled logic and runtime components; it excludes original game executables
and game assets.

Project source: https://github.com/kittycache9/OpenSA2
The repository's THIRD_PARTY_NOTICES.md records provenance and upstream sources.
Full component terms are included in licenses/.

The SDL 3.4.16 build is altered by this project's sampler-border and depth-clamp
patch. It is not the upstream SDL build. SDL uses the zlib license and includes
HIDAPI material with its original permissive notice.

DXVK 3.1.1 code and dxbc-spirv shader translation are compiled into the GPU player.
Their zlib/libpng and MIT notices are preserved. Remill 5.0.7 semantics and header
interfaces use Apache-2.0. libpng, zlib and Zstandard retain their upstream terms.
The Windows target uses MinGW-w64 and LLVM C++ runtimes. Their component notices
and Apache-2.0 with LLVM exceptions texts are preserved.

Wine-derived locale and code-page tables retain Wine's LGPL-2.1-or-later terms.
Unicode and CLDR attribution for the upstream data is included. No Wine DLL is
bundled. Windows system libraries, UCRT and the graphics driver are supplied by
the operating system.

See the original copyright lines in each component license. None are removed or
replaced by the OpenSA2 project copyright.
'''.encode()


def forbidden_hashes(path: Path | None) -> set[str]:
    if path is None:
        return set()
    obj = json.loads(path.read_text())
    hashes: set[str] = set()
    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == 'sha256' and isinstance(child, str) and re.fullmatch(r'[0-9a-fA-F]{64}', child):
                    hashes.add(child.lower())
                else:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(obj)
    return hashes


def validate(entries: dict[str, bytes], deny: list[str], forbidden: set[str]) -> None:
    for name, data in entries.items():
        parts = Path(name).parts
        if name.startswith(('/', '\\')) or '..' in parts or '\\' in name:
            raise SystemExit(f'Unsafe ZIP entry: {name}')
        if Path(name).suffix.lower() in ('.pdb', '.runrec', '.jsonl', '.log', '.png', '.prs'):
            raise SystemExit(f'Unexpected package file: {name}')
        if digest(data) in forbidden:
            raise SystemExit(f'Original game file in package: {name}')
        lowered = data.lower()
        for term in deny:
            if term and any(term.lower().encode(enc) in lowered for enc in ('utf-8', 'utf-16-le')):
                raise SystemExit(f'Private identity term in package file: {name}')


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--release', required=True, type=Path)
    ap.add_argument('--source-root', required=True, type=Path)
    ap.add_argument('--output', required=True, type=Path)
    ap.add_argument('--version', default='v0.1.0-alpha.1')
    ap.add_argument('--notices', type=Path, help='Shared binary third-party notices; defaults to the bundled Windows text.')
    ap.add_argument('--deny-file', type=Path)
    ap.add_argument('--forbidden-hashes', action='append', type=Path, default=[])
    ap.add_argument('--receipt', type=Path)
    args = ap.parse_args()
    if not re.fullmatch(r'v?[A-Za-z0-9][A-Za-z0-9.+_-]*', args.version):
        ap.error('Version must use letters, digits, dots, plus, underscore or hyphen.')
    args.release = args.release.resolve()
    args.source_root = args.source_root.resolve()
    args.output = args.output.resolve()
    if args.output.is_relative_to(args.release):
        ap.error('Output must be outside the input release.')
    head = json.loads((args.release/'release.manifest').read_text().splitlines()[0][2:])
    if head.get('target') != 'x86_64-windows':
        ap.error('Input must be a Windows release manifest.')
    mode = ((head.get('game') or {}).get('f80') or {}).get('mode') or 'native-x87'
    if mode != 'double':
        ap.error('This preview README requires the tested double-mode build.')
    entries = {name: (args.release/name).read_bytes() for name in RUNTIME}
    entries['README.txt'] = readme(args.version)
    entries['OpenSA2.cmd'] = b'@echo off\r\n"%~dp0bin\\sa2recomp.exe" %*\r\n'
    for name in ('LICENSE', 'NOTICE'):
        entries[name] = (args.source_root/name).read_bytes()
    entries['THIRD_PARTY_NOTICES.md'] = args.notices.read_bytes() if args.notices else notices()
    for name in LICENSES:
        entries['licenses/'+name] = (args.source_root/'licenses'/name).read_bytes()
    metadata = {'schema': 'opensa2-release/v1', 'project': 'OpenSA2', 'version': args.version,
                'target': 'windows-x86_64', 'floating_point': mode,
                'runtime': 'prebuilt development snapshot',
                'source': 'https://github.com/kittycache9/OpenSA2',
                'files': {name: {'sha256': digest(data), 'bytes': len(data)}
                          for name, data in sorted(entries.items())}}
    entries['release.manifest'] = (json.dumps(metadata, indent=2, sort_keys=True)+'\n').encode()
    deny = json.loads(args.deny_file.read_text()).get('terms', []) if args.deny_file else []
    forbidden = set().union(*(forbidden_hashes(p) for p in args.forbidden_hashes))
    validate(entries, deny, forbidden)
    prefix = 'OpenSA2-windows-x86_64/'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_name(args.output.name+'.part')
    with zipfile.ZipFile(tmp, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(prefix+name, date_time=(2026, 10, 13, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)
    with zipfile.ZipFile(tmp) as archive:
        if archive.testzip():
            raise SystemExit('ZIP readback failed.')
        if set(archive.namelist()) != {prefix+name for name in entries}:
            raise SystemExit('ZIP entry set differs from package plan.')
        actual = {name: archive.read(prefix+name) for name in entries}
        if actual != entries:
            raise SystemExit('ZIP readback bytes differ from package plan.')
        validate(actual, deny, forbidden)
    os.replace(tmp, args.output)
    receipt = {'schema': 'opensa2-windows-package-check/v1', 'version': args.version,
               'zip': str(args.output), 'bytes': args.output.stat().st_size,
               'sha256': digest(args.output.read_bytes()),
               'input_manifest_sha256': digest((args.release/'release.manifest').read_bytes()),
               'runtime_unchanged': {name: digest(entries[name]) for name in RUNTIME},
               'files': len(entries), 'privacy_terms': len(deny),
               'forbidden_hashes': len(forbidden), 'readback': True}
    if args.receipt:
        args.receipt.write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps(receipt, indent=2))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
