#!/usr/bin/env python3
"""Generate the player's exact host contract from verified pins and headers.

One contract per player binary (host/player/player.cpp, "Two binaries"):

  --host dxvk    (the default) sa2_player's: SDL's and DXVK's library hashes, dxvk.conf's
                 hash (--config), SDL's header version and revision
  --host sdlgpu  sa2_player_gpu's: SDL's library hash, version and revision; it reads
                 neither DXVK's library nor dxvk.conf, so the GPU player expects no DXVK file

The install (--install) is one of the pinned SDL3 configurations (tools/s5/build_host_deps.sh):
the patched one, whose library tools.sdl3 pins and whose DXVK tools.dxvk pins, or the stock
one, whose SDL library tools.sdl3-stock pins and whose DXVK, in its own prefix
(tools.sdl3-stock.dxvk_library), is held to tools.dxvk's hash, the same bytes (check_pins.py
holds it so). The configuration is the one whose pinned SDL library
the install's libSDL3.so.0 is; an install that is neither is refused, so a system SDL3, which
no pin holds, has no contract. The revision is the one SDL's revision header gives: with
SDL_VENDOR_INFO defined (the patched build's mark of an altered SDL, the patch set's identity,
which must be tools.sdl3.vendor_info) "<revision> (<vendor info>)", else "<revision>" (the
stock build, which must define none). The header's version is held to the configuration's own
entry (tools.sdl3.version, or tools.sdl3-stock.version). A pin the contract needs and the pins
lack is refused by name.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

# The contract's keys for each host, in the header's order.
CONTRACT_KEYS = {
    'dxvk': ('sdl_sha256', 'dxvk_sha256', 'dxvk_config_sha256', 'sdl_version', 'sdl_revision'),
    'sdlgpu': ('sdl_sha256', 'sdl_version', 'sdl_revision'),
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pin(pins, entry, key):
    """tools.<entry>.<key>, or a refusal naming the pin the contract needs."""
    value = pins.get(entry, {}).get(key) if isinstance(pins.get(entry), dict) else None
    if not isinstance(value, str) or not value:
        raise ValueError(f'tools/pins.json pins no {entry}.{key}, which the contract needs')
    return value


def generate(repo, install, config, output, host='dxvk'):
    if host not in CONTRACT_KEYS:
        raise ValueError(f'no host {host!r}: dxvk or sdlgpu')
    if (config is None) != (host == 'sdlgpu'):
        raise ValueError('--config names dxvk.conf: the DXVK contract needs it, the sdlgpu one reads none')
    pins_path = repo / 'tools/pins.json'
    pins = json.loads(pins_path.read_text())['tools']
    version_path = install / 'include/SDL3/SDL_version.h'
    revision_path = install / 'include/SDL3/SDL_revision.h'
    version_source = version_path.read_text()
    parts = []
    for name in ('MAJOR', 'MINOR', 'MICRO'):
        found = re.findall(r'^#define SDL_' + name + r'_VERSION\s+(\d+)\s*$', version_source, re.M)
        if len(found) != 1:
            raise ValueError('ambiguous SDL version header')
        parts.append(int(found[0]))
    revision_source = revision_path.read_text()
    revisions = re.findall(r'^#define SDL_REVISION "([^"\n]*)"\s*$', revision_source, re.M)
    if len(revisions) != 1 or not revisions[0]:
        raise ValueError('missing opaque default SDL revision')
    vendors = re.findall(r'^#define SDL_VENDOR_INFO "([^"\n]*)"\s*$', revision_source, re.M)
    if len(vendors) > 1 or len(re.findall(r'^#define SDL_VENDOR_INFO\b', revision_source, re.M)) != len(vendors):
        raise ValueError('ambiguous SDL_VENDOR_INFO in the SDL revision header')
    if vendors and re.findall(r'^#define SDL_REVISION "([^"\n]*) \(" SDL_VENDOR_INFO "\)"\s*$', revision_source,
                              re.M) != revisions:
        raise ValueError('the SDL revision header does not append SDL_VENDOR_INFO to its revision')
    revision = f'{revisions[0]} ({vendors[0]})' if vendors else revisions[0]
    # The configuration: the pinned SDL library the install's libSDL3.so.0 is. Each library is
    # (entry, path key) for its file and (entry, sha256 key) for its pin. The stock prefix's DXVK
    # is the patched configuration's bytes, held to tools.dxvk's pin as check_pins.py holds it
    # (verify_sdl3_stock: one pin per distinct file), never to a hash of tools.sdl3-stock's own.
    installed = (install / 'lib/libSDL3.so.0').resolve(strict=True)
    candidates = {'patched': {'sdl3': (('sdl3', 'library'), ('sdl3', 'library_sha256')),
                              'dxvk': (('dxvk', 'library'), ('dxvk', 'library_sha256'))},
                  'stock': {'sdl3': (('sdl3-stock', 'library'), ('sdl3-stock', 'library_sha256')),
                            'dxvk': (('sdl3-stock', 'dxvk_library'), ('dxvk', 'library_sha256'))}}
    chosen = []
    for c, libs in candidates.items():
        entry, key = libs['sdl3'][0]
        named = pins.get(entry)
        if isinstance(named, dict) and isinstance(named.get(key), str) and (repo / named[key]).resolve() == installed:
            chosen.append(c)
    if not chosen:
        raise ValueError('selected SDL prefix is not the pinned input: the install\'s libSDL3.so.0 is neither '
                         'the patched configuration\'s (tools.sdl3) nor the stock one\'s (tools.sdl3-stock)')
    if len(chosen) > 1:
        raise ValueError('selected SDL prefix is not one configuration: the install\'s libSDL3.so.0 is both the '
                         'patched configuration\'s (tools.sdl3) and the stock one\'s (tools.sdl3-stock), which '
                         'must name two files')
    configuration = chosen[0]
    version_entry = candidates[configuration]['sdl3'][0][0]
    want_version = pin(pins, version_entry, 'version')
    if '.'.join(map(str, parts)) != want_version:
        raise ValueError(f'SDL header version {".".join(map(str, parts))} disagrees with tools/pins.json '
                         f'({version_entry}.version {want_version})')
    want_vendor = (pins.get('sdl3') or {}).get('vendor_info') if configuration == 'patched' else None
    if (vendors[0] if vendors else None) != want_vendor:
        raise ValueError(f'the {configuration} configuration\'s SDL revision header defines SDL_VENDOR_INFO '
                         f'{vendors or "none"}, not {want_vendor or "none"}')
    if 'dxvk_library_sha256' in pins.get('sdl3-stock', {}):
        raise ValueError('tools.sdl3-stock pins dxvk_library_sha256: the stock prefix\'s DXVK is held to '
                         'tools.dxvk.library_sha256, never to a hash of its own')
    libraries = {}
    for name, ((entry, key), (digest_entry, digest)) in candidates[configuration].items():
        if name == 'dxvk' and host != 'dxvk':
            continue
        selected = repo / pin(pins, entry, key)
        path = selected.resolve(strict=True)
        actual = sha(path)
        want = pin(pins, digest_entry, digest)
        if not re.fullmatch('[0-9a-f]{64}', want) or actual != want:
            raise ValueError(f'{name} library disagrees with tools/pins.json ({digest_entry}.{digest})')
        libraries[name] = {'selected_path': str(selected), 'path': str(path), 'sha256': actual}
    if installed != Path(libraries['sdl3']['path']):
        raise ValueError('selected SDL prefix is not the pinned input')
    values = {
        'sdl_sha256': libraries['sdl3']['sha256'],
        'dxvk_sha256': libraries.get('dxvk', {}).get('sha256'),
        'dxvk_config_sha256': sha(config) if config is not None else None,
        'sdl_version': parts[0] * 1000000 + parts[1] * 1000 + parts[2],
        'sdl_revision': revision,
    }
    contract = {k: values[k] for k in CONTRACT_KEYS[host]}
    header = '#pragma once\nnamespace sa2_host_expected {\n'
    for name, value in contract.items():
        if isinstance(value, str):
            header += 'inline constexpr char ' + name + '[] = ' + json.dumps(value) + ';\n'
        else:
            header += 'inline constexpr int ' + name + ' = ' + str(value) + ';\n'
    header += '}\n'
    output.mkdir(parents=True, exist_ok=True)
    target = output / 'sa2_host_expectations.h'
    target.write_text(header)
    sources = [pins_path, version_path, revision_path, *([config] if config is not None else []),
               Path(__file__).resolve()]
    receipt = {
        'contract': contract,
        'libraries': libraries,
        'sources': [{'path': str(p), 'sha256': sha(p)} for p in sources],
        'header': {'path': str(target), 'sha256': sha(target)},
        'scope': 'Generated expectations only. No loaded-library or startup proof.',
        'sdl3_configuration': configuration,
    }
    if host != 'dxvk':
        receipt['host'] = host
    (output / 'expectations.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--install', type=Path, required=True)
    parser.add_argument('--config', type=Path, help="dxvk.conf (--host dxvk only)")
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--host', choices=sorted(CONTRACT_KEYS), default='dxvk')
    args = parser.parse_args()
    try:
        generate(args.repo, args.install, args.config, args.out, args.host)
    except ValueError as exc:
        parser.exit(2, f'generate_expectations.py: {exc}\n')
