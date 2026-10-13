#!/usr/bin/env python3
"""Write or freshly verify the registered Metal Harbor two-host frame receipt.

This consumes a completed comparison; it launches no player. R4 owns full
original-account, build, settings, reference and omission-control validation.
The receipt has G5's existing host-compare schema. R5 and the live R6 headless
and broken-renderer obligations remain separate from retained-run verification.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import types

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'AGENTS.md').is_file())
SELF = 'tools/s5/host_compare.py'
COMPARER_SHA256 = '49392315ba9705a96ce8a7fe5adcae7ed13bbede7251926e1859013a276abc31'
RECORDING = 'private/generated/s5/pushC/record/metalharbor/present-run.json'
RECORDING_SHA256 = '7eb5e35fee37cca00c372aed090ca819a2d3482d5fcf49e5db4c48913cfc73b9'
CODE = ('tools/s5/play_report.py', 'tools/s5/stream_dump.py', 'host/player/build_record.py',
        'tools/s5/compare_hosts.py', 'Documentation/scripts/check_s5_exit.py', SELF)
EXTRA = (SELF, 'Documentation/scripts/check_s5_exit.py', 'Documentation/replays.json',
         'Documentation/decisions/2026-09-26-S5-host-sdl3-dxvk.md', RECORDING)
RAN_BY = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def identity(path: Path) -> dict:
    s = path.stat()
    if not stat.S_ISREG(s.st_mode):
        raise ValueError(f'not a regular input: {path}')
    with path.open('rb') as file:
        digest = hashlib.file_digest(file, 'sha256').hexdigest()
    return {'sha256': digest, 'real_path': str(path.resolve()), 'mode': s.st_mode,
            'device': s.st_dev, 'inode': s.st_ino}


def code_inputs() -> dict:
    held = {str(ROOT / p): identity(ROOT / p) for p in CODE}
    if held[str(ROOT / SELF)]['sha256'] != RAN_BY:
        raise ValueError('host_compare.py changed since it started')
    if held[str(ROOT / 'tools/s5/compare_hosts.py')]['sha256'] != COMPARER_SHA256:
        raise ValueError('the registered R4 comparer is not adopted')
    return held


def actors(held: dict):
    """Execute the five fixed actors from first-held bytes, ignoring their pyc."""
    raw = {}
    for name in CODE[:-1]:
        path = ROOT / name
        raw[name] = path.read_bytes()
        if hashlib.sha256(raw[name]).hexdigest() != held[str(path)]['sha256'] or identity(path) != held[str(path)]:
            raise ValueError(f'first-held actor changed before import: {name}')
    old_spec = importlib.util.spec_from_file_location
    old_modules = {n: sys.modules.get(n) for n in ('play_report', 'stream_dump')}
    def load(name, path):
        module = types.ModuleType(name)
        module.__file__ = str(ROOT / path)
        sys.modules[name] = module
        exec(compile(raw[path], module.__file__, 'exec'), module.__dict__)
        return module
    class BuildRecordLoader:
        def create_module(self, spec):
            return None
        def exec_module(self, module):
            name = 'host/player/build_record.py'
            exec(compile(raw[name], str(ROOT / name), 'exec'), module.__dict__)
    def fixed_spec(name, location, *args, **kwargs):
        spec = old_spec(name, location, *args, **kwargs)
        if Path(location).resolve() == (ROOT / 'host/player/build_record.py').resolve():
            spec.loader = BuildRecordLoader()
        return spec
    try:
        load('play_report', 'tools/s5/play_report.py')
        load('stream_dump', 'tools/s5/stream_dump.py')
        importlib.util.spec_from_file_location = fixed_spec
        comparison = load('_s5_r6_comparison', 'tools/s5/compare_hosts.py')
        gate = load('_s5_r6_gate', 'Documentation/scripts/check_s5_exit.py')
    finally:
        importlib.util.spec_from_file_location = old_spec
        for name, module in old_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    if any(identity(Path(path)) != item for path, item in held.items()):
        raise ValueError('first-held code changed during import')
    return comparison, gate


def object_json(path: Path) -> dict:
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError(f'duplicate JSON member {key!r} in {path}')
            value[key] = item
        return value
    value = json.loads(path.read_text(), object_pairs_hook=pairs,
                       parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f'nonfinite JSON {value}')))
    if not isinstance(value, dict):
        raise ValueError(f'not a JSON object: {path}')
    return value


def relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve()))


def reference(path: Path, ch) -> dict:
    return {'path': relative(path), 'sha256': ch.sha256(path)}


def mismatch_detail(run: Path, ch, gate) -> str:
    """A raw first-difference diagnostic; this never grants acceptance."""
    paths = [('dxvk-native', run / 'dxvk/frames.y4m.times.tsv'),
             ('sdl3-gpu', run / 'sdlgpu/frames.y4m.times.tsv'),
             ('registered-reference', ROOT / ch.METAL_HARBOR['reference'])]
    try:
        tables = {}
        for name, path in paths:
            raw = gate.table(path, gate.TIMES_HEADER, name)
            parsed = gate.integers(raw, gate.TIMES_HEADER[:-1], name)
            for row, values in zip(raw, parsed):
                values['bgrx_hash'] = row['bgrx_hash']
            tables[name] = parsed
        for first, second in (('dxvk-native', 'sdl3-gpu'), ('registered-reference', 'dxvk-native')):
            a, b = tables[first], tables[second]
            for row, (x, y) in enumerate(zip(a, b), 1):
                for field in gate.COMPARED:
                    if x[field] != y[field]:
                        return (f"first mismatch row {row} frame {x['frame']} virtual_us {x['virtual_us']}: "
                                f'{field} {first} {x[field]} {second} {y[field]}')
            if len(a) != len(b):
                return f'first missing row {min(len(a), len(b)) + 1}: {first} {len(a)} rows, {second} {len(b)} rows'
    except (OSError, ValueError, KeyError, TypeError, gate.Refused) as error:
        return f'raw mismatch diagnostic refused: {error}'
    return 'no six-field mismatch; see the actual comparison failures'


def first_run(run: Path, first_code: dict) -> dict:
    """Capture physical paths with stdlib only, before executable actors load.

    The common collector later selects semantic paths from this first map.
    That selection must never replace an identity after an actor executes.
    """
    run = run.resolve()
    relative(run)
    document = run / 'compare.json'
    document_identity = identity(document)
    old = object_json(document)
    paths = {ROOT / p for p in (*CODE, *EXTRA, *old['inputs']['pipeline'])}
    paths.update(ROOT / p for p in ('host/sa2_stream.h', 'host/dxvk_smoke.cpp', 'tools/pins.json'))
    for directory in ('host/player', 'host/d3d9gpu'):
        paths.update(p for p in (ROOT / directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts)
    paths.update(p for p in run.rglob('*') if p.is_file())
    paths.add(ROOT / old['stream']['path'])
    paths.add(ROOT / old['inputs']['census']['path'])
    for check in old.get('checks') or []:
        reference = (check.get('detail') or {}).get('reference') if check.get('check') == 'h' else None
        if reference:
            paths.add(ROOT / reference)
    build = ROOT / old['inputs']['build']
    for name in ('sa2_player.json', 'sa2_player_gpu.json', 'player_tools.json', 'd3d9gpu_tools.json'):
        path = build / name
        record = object_json(path)
        paths.add(path)
        paths.update(ROOT / p for p in record.get('sources', {}))
        for kind in ('binaries', 'generated', 'depfiles'):
            paths.update(build / p for p in record.get(kind, {}))
        paths.update(Path(p) for p in record.get('external', {}))
    for path in run.glob('*/report.json'):
        report = object_json(path)
        for item in (report.get('files') or {}).values():
            if isinstance(item, dict) and item.get('real_path'):
                paths.add(Path(item['real_path']))
    for path in run.glob('*/d3d9gpu.json'):
        keys = (object_json(path).get('pipeline') or {}).get('keys_file')
        if keys:
            paths.add(Path(keys))
    held = {str(path.absolute()): identity(path) for path in sorted(paths)}
    if identity(document) != document_identity or any(held.get(path) != item for path, item in first_code.items()):
        raise ValueError('first run/document/code identities changed during initial capture')
    return {'run': run, 'document': document_identity, 'old': old, 'inputs': held}


def prepare(run: Path, ch, gate, first_code: dict, original: dict):
    """Revalidate without rewriting compare.json, retaining its first identity."""
    run = run.resolve()
    relative(run)
    document = run / 'compare.json'
    document_sha = original['document']['sha256']
    old = original['old']
    if run != original['run'] or identity(document) != original['document']:
        raise ValueError('first comparison identity changed during actor setup')
    if why := ch.receipt_refusal(old, str(run)):
        raise ValueError(why)
    guarded = copy.deepcopy(old)
    guarded['inputs']['pipeline'].update({p: original['inputs'][str((ROOT / p).absolute())]['sha256'] for p in EXTRA})
    # This internal row holds compare.json's mode/inode as well as its digest.
    # It is not written into the public receipt or the original comparison.
    guarded['outputs']['compare.json'] = document_sha
    current_inputs = ch.receipt_inputs(run, guarded)
    if any(original['inputs'].get(path) != item for path, item in current_inputs.items()):
        raise ValueError('first semantic input graph changed during actor setup')
    inputs = {path: original['inputs'][path] for path in current_inputs}
    # The first source graph survives imports and every later collector call.
    if any(inputs.get(path) != item for path, item in first_code.items()):
        raise ValueError('first-held code identity changed before run validation')
    held = [(run, guarded, inputs, document_sha)]
    if not ch.receipt_inputs_unchanged(held):
        raise ValueError('initial run/document graph changed')
    recording = object_json(ROOT / RECORDING)
    if (ch.sha256(ROOT / RECORDING) != RECORDING_SHA256 or recording.get('schema') != gate.RECORDING_SCHEMA
            or recording.get('replay') != 'metalharbor' or recording.get('ok') is not True
            or gate.field(recording, gate.STREAM_OF_RECORDING) != ch.METAL_HARBOR['stream_sha256']):
        raise ValueError('not the held original Metal Harbor recording')
    if 'metalharbor' not in gate.registered(ROOT):
        raise ValueError('Metal Harbor is not registered')
    status, fresh = ch.checked_run(run)
    if not ch.receipt_inputs_unchanged(held):
        raise ValueError('run/document graph changed during validation')
    if status != 0 or not isinstance(fresh, dict):
        failures = [str(f) for c in (fresh or {}).get('checks', []) for f in c.get('failures', [])]
        raise ValueError('the actual full comparison did not reverify: ' + '; '.join(failures[:8])
                         + '; ' + mismatch_detail(run, ch, gate))
    if (why := ch.receipt_refusal(fresh, str(run))):
        raise ValueError(why)
    if (ch.acceptance_marker(fresh) != 'R4_METALHARBOR_OK' or fresh.get('verdict_with_controls') != 'PASS'
            or fresh.get('acceptance_scope') != 'r2-draws-full' or type(fresh.get('max_frames')) is not int
            or fresh['max_frames'] != 0 or fresh.get('stream', {}).get('sha256') != ch.METAL_HARBOR['stream_sha256']):
        raise ValueError('not a fresh complete registered Metal Harbor comparison with exercised controls')
    hosts = []
    for role, host, file in (('dxvk', 'dxvk-native', 'libdxvk_d3d9'), ('sdlgpu', 'sdl3-gpu', 'player')):
        report = object_json(run / role / 'report.json')
        renderer = Path(report['files'][file]['real_path'])
        frames = Path(report['frames_out']['times'])
        if not frames.is_absolute():
            frames = ROOT / frames
        if frames.resolve() != (run / role / 'frames.y4m.times.tsv').resolve():
            raise ValueError(f'{host}: frames table is not its role-owned output')
        hosts.append({'host': host, 'renderer': reference(renderer, ch),
                      'report': reference(run / role / 'report.json', ch), 'frames': reference(frames, ch)})
    selected = gate.selection(ROOT)
    done = [gate.rendered(ROOT, entry, ch.METAL_HARBOR['stream_sha256']) for entry in hosts]
    if sorted(host for host, _, _ in done) != sorted(selected) or len({r for _, r, _ in done}) != 2:
        raise ValueError('not the selected two independent renderers')
    a, b = (rows for _, _, rows in done)
    if len(a) != len(b):
        raise ValueError(f'Metal Harbor frame counts differ: {len(a)} versus {len(b)}')
    for x, y in zip(a, b):
        for field in gate.COMPARED:
            if x[field] != y[field]:
                raise ValueError(f"Metal Harbor frame {x['frame']} virtual_us {x['virtual_us']}: {field} dxvk-native {x[field]} sdl3-gpu {y[field]}")
    if len({r['bgrx_hash'] for r in a}) < gate.MIN_DISTINCT_PICTURES:
        raise ValueError('equal constant pictures do not establish frame comparison')
    # assemble() stamps a fresh wall-clock completion on each nonwriting check.
    # The actual recorded document stays bound above; only that new observation
    # time is excluded from the deterministic freshly checked semantic digest.
    semantic = dict(fresh)
    semantic.pop('finished', None)
    # The recording stays in the held internal graph and its evidence reference.
    # Public pipeline pins name producing sources present in the publishing commit.
    published_pipeline = dict(guarded['inputs']['pipeline'])
    del published_pipeline[RECORDING]
    receipt = {'schema': gate.COMPARE_SCHEMA, 'generated_by': SELF,
               'inputs': {'pipeline': published_pipeline,
                          'comparison': reference(document, ch),
                          'checked_comparison_sha256': hashlib.sha256(json.dumps(semantic, sort_keys=True, allow_nan=False).encode()).hexdigest()},
               'run': relative(run), 'replay': 'metalharbor', 'stream_sha256': fresh['stream']['sha256'],
               'recording': reference(ROOT / RECORDING, ch), 'hosts': hosts,
               'marker': 'R6_HOST_COMPARE_OK', 'scope': 'registered full Metal Harbor retained-run frame equality'}
    if not ch.receipt_inputs_unchanged(held):
        raise ValueError('run/document graph changed while building the receipt')
    return receipt, held


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv:
        argv = ['check', '--receipt', str(ROOT / 'Documentation/receipts/s5-host-compare-v1.json')]
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    write = commands.add_parser('write')
    write.add_argument('--run', type=Path, required=True)
    write.add_argument('--out', type=Path, required=True)
    check = commands.add_parser('check')
    check.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        first = code_inputs()
        receipt_identity = None
        if args.command == 'check':
            receipt_identity = identity(args.receipt)
            old = object_json(args.receipt)
            if identity(args.receipt) != receipt_identity:
                raise ValueError('first receipt identity changed during its read')
            run = ROOT / old['run']
        else:
            run = args.run
            if os.path.lexists(args.out) or not args.out.parent.is_dir():
                raise ValueError('output exists or its parent is absent')
            relative(args.out)
            if args.out.resolve().is_relative_to(run.resolve()):
                raise ValueError('output is inside the input run')
        original = first_run(run, first)
        ch, gate = actors(first)
        if receipt_identity is not None and identity(args.receipt) != receipt_identity:
            raise ValueError('first receipt identity changed during actor setup')
        try:
            receipt, held = prepare(run, ch, gate, first, original)
        except gate.Refused as error:
            raise ValueError(str(error)) from error
        if args.command == 'check':
            if old != receipt or identity(args.receipt) != receipt_identity:
                raise ValueError('receipt differs from fresh actual evidence or changed during its check')
            if not ch.receipt_inputs_unchanged(held):
                raise ValueError('input changed at final nonwriting check')
            if identity(args.receipt) != receipt_identity:
                raise ValueError('first receipt identity changed during final input observation')
        else:
            if args.out.resolve().is_relative_to((ROOT / held[0][1]['inputs']['build']).resolve()):
                raise ValueError('output is inside the input build')
            text = json.dumps(receipt, indent=1, allow_nan=False) + '\n'
            if not ch.publish_receipt(args.out, text, held):
                return 1
        print('R6_HOST_COMPARE_OK')
        return 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        print(f'host comparison refused: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
