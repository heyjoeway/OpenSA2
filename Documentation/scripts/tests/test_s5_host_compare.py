"""The real R6 writer/checker on tiny physically closed CPU specimens.

Only the registered SHA/count policy and compiler/build/pixels/hardware records
are authored stand-ins. Actual R4 account/provenance/settings/control validators,
gate rendering contract and descriptor-held publisher execute. No child runs.
"""
import copy
import builtins
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import types

import pytest

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'AGENTS.md').is_file())


def raw_module(name, path):
    module = types.ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    exec(compile(path.read_bytes(), str(path), 'exec'), module.__dict__)
    return module


HS = raw_module('_test_r6_writer', ROOT / 'tools/s5/host_compare.py')
CH, GATE = HS.actors(HS.code_inputs())
RAW_ACTORS = HS.actors
old = sys.modules.get('compare_hosts')
sys.modules['compare_hosts'] = CH
T = raw_module('_test_r6_mechanical_fixtures', ROOT / 'Documentation/scripts/tests/test_s5_compare_hosts.py')
if old is None:
    sys.modules.pop('compare_hosts', None)
else:
    sys.modules['compare_hosts'] = old


@pytest.fixture(autouse=True)
def no_children(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('R6 CPU proof attempted a child or socket')
    for name in ('Popen', 'run', 'call', 'check_call', 'check_output'):
        monkeypatch.setattr(subprocess, name, blocked)
    monkeypatch.setattr(os, 'system', blocked)
    monkeypatch.setattr(os, 'fork', blocked)
    monkeypatch.setattr(socket, 'socket', blocked)


@pytest.fixture
def specimen(monkeypatch, tmp_path):
    # This existing writer never calls the test module's Git/compiler fixtures.
    root, build, run, other = T.publication_fixture(monkeypatch, tmp_path)
    for name in HS.EXTRA:
        if name == HS.RECORDING:
            continue
        destination = root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / name).read_bytes())
    monkeypatch.setattr(HS, 'ROOT', root)
    # Both entries execute real freshly checked code with test-only ROOT/policy.
    monkeypatch.setattr(HS, 'actors', lambda first: (CH, GATE))
    recording = root / HS.RECORDING
    recording.parent.mkdir(parents=True, exist_ok=True)
    source = run / 'original.sa2s'
    recording.write_text(json.dumps({'schema': 'sa2-present-run/v3', 'replay': 'metalharbor',
                                    'ok': True, 'ready': {'stream_sha256': CH.sha256(source)}}))
    monkeypatch.setattr(HS, 'RECORDING_SHA256', CH.sha256(recording))
    policy = dict(CH.METAL_HARBOR, stream_sha256=CH.sha256(source), frames=4,
                  reference=str(run / 'reference.tsv'), reference_sha256=CH.sha256(run / 'reference.tsv'))
    monkeypatch.setattr(CH, 'METAL_HARBOR', policy)
    # Real library references rather than forged repeated hash strings.
    for role in ('dxvk', 'sdlgpu', 'control-geometry-noop', 'control-count-draw'):
        path = run / role / 'report.json'
        report = json.loads(path.read_text())
        report.update(frame_records=4, stream_records_read_to_end=True,
                      stream_problems={'count': 0}, notes={'count': 0})
        for name, item in report['files'].items():
            if isinstance(item, dict):
                item['sha256'] = CH.sha256(Path(item['real_path']))
        report['host_identity']['expected_sdl_sha256'] = report['files']['libSDL3']['sha256']
        if role == 'dxvk':
            report['host_identity']['expected_dxvk_sha256'] = report['files']['libdxvk_d3d9']['sha256']
            report['host_identity']['expected_config_sha256'] = report['files']['dxvk_conf']['sha256']
        rows = GATE.table(run / role / 'frames.y4m.times.tsv', GATE.TIMES_HEADER, 'literal fixture')
        report['frames_out'].update(frames=4, times_rows=4, bgrx_digest=GATE.frames_digest(rows))
        path.write_text(json.dumps(report))
    pins_path = root / 'tools/pins.json'
    pins = json.loads(pins_path.read_text())
    pins['tools']['dxvk']['library_sha256'] = CH.sha256(root / 'runtime/libdxvk_d3d9')
    pins_path.write_text(json.dumps(pins))
    # Refresh authored build-source pins after this explicit fixture-only pin edit.
    for name in CH.BUILD_RECORDS:
        path = build / name
        data = json.loads(path.read_text())
        data['sources'] = {p: CH.sha256(root / p) for p in data['sources']}
        path.write_text(json.dumps(data))
    matrix = run / 'matrix/run.json'
    m = json.loads(matrix.read_text())
    m['dxvk_library'] = {'path': str(root / 'runtime/libdxvk_d3d9'), 'sha256': pins['tools']['dxvk']['library_sha256']}
    matrix.write_text(json.dumps(m))
    monkeypatch.setattr(CH, 'BOOT_STREAM_SHA256', 'f' * 64)
    refresh(run)
    status, doc = CH.checked_run(run)
    assert status == 0 and doc['marker'] == 'R4_METALHARBOR_OK', doc
    (run / 'compare.json').write_text(json.dumps(doc))
    output = root / GATE.COMPARE_RECEIPT
    output.parent.mkdir(exist_ok=True)
    return root, build, run, output


def refresh(run):
    old = json.loads((run / 'compare.json').read_text())
    old['outputs'] = CH.recorded_outputs(run)
    old['inputs']['pipeline'] = CH.pipeline()
    (run / 'compare.json').write_text(json.dumps(old))


def modify(path, change):
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))


def test_actual_cli_writes_gate_schema_and_checks_read_only(specimen):
    root, build, run, output = specimen
    raw_before = {str(p): CH.sha256(p) for p in run.rglob('*') if p.is_file()}
    assert HS.main(['write', '--run', str(run), '--out', str(output)]) == 0
    receipt = json.loads(output.read_text())
    assert (receipt['schema'], receipt['generated_by'], receipt['replay'], receipt['marker']) == (
        'sa2-s5-host-compare/v1', 'tools/s5/host_compare.py', 'metalharbor', 'R6_HOST_COMPARE_OK')
    assert [h['host'] for h in receipt['hosts']] == ['dxvk-native', 'sdl3-gpu']
    assert GATE.identical(root).met is True
    assert HS.main(['check', '--receipt', str(output)]) == 0
    assert raw_before == {str(p): CH.sha256(p) for p in run.rglob('*') if p.is_file()}
    assert HS.main(['write', '--run', str(run), '--out', str(output)]) == 1


@pytest.mark.parametrize('argv', [[], None])
def test_no_arguments_check_default_receipt_without_writes(specimen, monkeypatch, capsys, argv):
    root, build, run, output = specimen
    assert HS.main(['write', '--run', str(run), '--out', str(output)]) == 0
    before = {str(p): (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns)
              for p in run.rglob('*') if p.is_file()}
    receipt_before = (output.read_bytes(), output.stat().st_ino, output.stat().st_mtime_ns)
    monkeypatch.setattr(sys, 'argv', ['tools/s5/host_compare.py'])
    capsys.readouterr()
    assert HS.main(argv) == 0
    assert capsys.readouterr().out == 'R6_HOST_COMPARE_OK\n'
    assert receipt_before == (output.read_bytes(), output.stat().st_ino, output.stat().st_mtime_ns)
    assert before == {str(p): (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns)
                      for p in run.rglob('*') if p.is_file()}


@pytest.mark.parametrize('malformed', [False, True])
def test_no_arguments_refuse_absent_or_invalid_default_receipt(specimen, capsys, malformed):
    root, build, run, output = specimen
    if malformed:
        output.write_bytes(b'{invalid receipt}\n')
    before = output.read_bytes() if malformed else None
    assert HS.main([]) == 1
    captured = capsys.readouterr()
    assert captured.out == '' and 'host comparison refused:' in captured.err
    if malformed:
        assert output.read_bytes() == before
    else:
        assert not output.exists()


@pytest.mark.parametrize('field,value', [('frame', '2'), ('seq', '8'), ('virtual_us', '1'),
    ('guest_frames', '1'), ('polls', '1'), ('bgrx_hash', '0x000000000000000c')])
@pytest.mark.parametrize('coherent', [False, True])
def test_each_semantic_column_refuses_even_coherent_hosts(specimen, field, value, coherent, capsys):
    root, build, run, output = specimen
    for role in (('dxvk', 'sdlgpu') if coherent else ('sdlgpu',)):
        T.aa_tsv(run / role / 'frames.y4m.times.tsv', lambda rows: rows[0].update({field: value}))
    refresh(run)
    assert HS.main(['write', '--run', str(run), '--out', str(output)]) == 1
    assert not output.exists()
    detail = capsys.readouterr().err
    assert 'first mismatch row 1 frame 1 virtual_us 0' in detail
    assert field in detail and value in detail


@pytest.mark.parametrize('target', ['recording', 'source', 'reference', 'no-end', 'partial', 'stats-only',
    'vk-query', 'vulkan-api', 'controls', 'provenance', 'wrong-vsync', 'zero-errors', 'missing-report'])
def test_actual_scope_and_contract_refusals(specimen, target):
    root, build, run, output = specimen
    if target in ('recording', 'source', 'reference'):
        p = root / HS.RECORDING if target == 'recording' else run / ('original.sa2s' if target == 'source' else 'reference.tsv')
        p.write_bytes(p.read_bytes() + b'changed')
    elif target == 'missing-report':
        (run / 'sdlgpu/report.json').unlink()
    elif target == 'controls':
        modify(run / 'compare.json', lambda d: d.update(controls={'run': False}))
    elif target == 'provenance':
        modify(build / 'sa2_player_gpu.json', lambda d: d['sources'].update({'unknown.c': '0' * 64}))
    elif target == 'partial':
        modify(run / 'compare.json', lambda d: d.update(acceptance_scope='r2-draws-partial', max_frames=1))
    elif target in ('vk-query', 'vulkan-api'):
        modify(run / 'sdlgpu/d3d9gpu.json', lambda d: d['sdl_gpu'].update(vk_query_ok=False) if target == 'vk-query'
               else d['sdl_gpu']['vulkan_options'].pop('api_version'))
    else:
        def change(d):
            if target == 'no-end': d.update(stream_records_read_to_end=False, records=dict(d['records'], end=0))
            if target == 'stats-only': d['options']['stats_only'] = True
            if target == 'wrong-vsync': d['options']['vsync'] = True
            if target == 'zero-errors': d['errors'] = 1
        modify(run / 'sdlgpu/report.json', change)
    refresh(run)
    assert HS.main(['write', '--run', str(run), '--out', str(output)]) == 1
    assert not output.exists()


@pytest.mark.parametrize('target', ['source', 'recording', 'raw-report', 'compare', 'code'])
def test_late_actual_checker_change_cannot_publish(specimen, monkeypatch, target):
    root, build, run, output = specimen
    checked = CH.checked_run
    def drift(directory):
        status, doc = checked(directory)
        p = {'source': run / 'original.sa2s', 'recording': root / HS.RECORDING,
             'raw-report': run / 'sdlgpu/report.json', 'compare': run / 'compare.json',
             'code': root / 'host/player/build_record.py'}[target]
        p.write_bytes(p.read_bytes() + b' ')
        return status, doc
    monkeypatch.setattr(CH, 'checked_run', drift)
    assert HS.main(['write', '--run', str(run), '--out', str(output)]) == 1
    assert not output.exists()


def test_saved_receipt_claim_and_raw_drift_refuse_without_writes(specimen):
    root, build, run, output = specimen
    assert HS.main(['write', '--run', str(run), '--out', str(output)]) == 0
    modify(output, lambda d: d.update(marker='forged PASS'))
    original = output.read_bytes()
    assert HS.main(['check', '--receipt', str(output)]) == 1
    assert output.read_bytes() == original


def test_foreign_output_created_during_preparation_survives(specimen, monkeypatch):
    root, build, run, output = specimen
    checked = CH.checked_run
    def competitor(directory):
        result = checked(directory)
        output.write_bytes(b'other writer')
        return result
    monkeypatch.setattr(CH, 'checked_run', competitor)
    assert HS.main(['write', '--run', str(run), '--out', str(output)]) == 1
    assert output.read_bytes() == b'other writer'


def test_first_source_anchor_survives_actor_setup(specimen, monkeypatch):
    root, build, run, output = specimen
    def drift(first):
        path = root / 'host/player/build_record.py'
        path.write_bytes(path.read_bytes() + b'\n')
        return CH, GATE
    monkeypatch.setattr(HS, 'actors', drift)
    assert HS.main(['write', '--run', str(run), '--out', str(output)]) == 1
    assert not output.exists()


@pytest.mark.parametrize('field', ['schema', 'replay', 'ok', 'stream'])
def test_rehashed_recording_must_still_be_original_producer(specimen, monkeypatch, field):
    root, build, run, output = specimen
    path = root / HS.RECORDING
    def change(data):
        if field == 'stream': data['ready']['stream_sha256'] = 'e' * 64
        else: data[field] = {'schema': 'wrong', 'replay': 'boot', 'ok': False}[field]
    modify(path, change)
    monkeypatch.setattr(HS, 'RECORDING_SHA256', CH.sha256(path))
    assert HS.main(['write', '--run', str(run), '--out', str(output)]) == 1
    assert not output.exists()


@pytest.mark.parametrize('kind', ['bad-header', 'bad-hash', 'missing-row', 'constant-pictures', 'short-pair'])
def test_literal_table_boundaries_refuse(specimen, kind):
    root, build, run, output = specimen
    for role in ('dxvk', 'sdlgpu'):
        p = run / role / 'frames.y4m.times.tsv'
        lines = p.read_text().splitlines()
        if kind == 'bad-header': lines[0] = lines[0].replace('seq', 'sequence')
        if kind == 'bad-hash': lines[1] = lines[1].replace('0x0000000000000001', 'not-a-pixel-hash')
        if kind in ('missing-row', 'short-pair'): lines.pop()
        if kind == 'constant-pictures':
            lines = [lines[0]] + [l.rsplit('\t', 1)[0] + '\t0x0000000000000001' for l in lines[1:]]
        p.write_text('\n'.join(lines) + '\n')
    refresh(run)
    assert HS.main(['write', '--run', str(run), '--out', str(output)]) == 1
    assert not output.exists()


def test_current_raw_actors_ignore_project_module_and_pyc_cache(specimen, monkeypatch):
    root, build, run, output = specimen
    import importlib._bootstrap_external
    for name in ('play_report', 'stream_dump'):
        poison = types.ModuleType(name)
        poison.sha256_file = lambda p: 'poisoned'
        monkeypatch.setitem(sys.modules, name, poison)
    for name in ('tools/s5/play_report.py', 'tools/s5/stream_dump.py', 'host/player/build_record.py'):
        path = root / name
        pyc = Path(importlib.util.cache_from_source(str(path)))
        pyc.parent.mkdir(parents=True, exist_ok=True)
        data = importlib._bootstrap_external._code_to_timestamp_pyc(
            compile("raise ValueError('cached project actor executed')", str(path), 'exec'),
            int(path.stat().st_mtime), path.stat().st_size)
        pyc.write_bytes(data)
    first = HS.code_inputs()
    original_hook = importlib.util.spec_from_file_location
    ch, gate = RAW_ACTORS(first)
    assert importlib.util.spec_from_file_location is original_hook
    assert sys.modules['play_report'].sha256_file(Path('ignored')) == 'poisoned'
    assert ch.stream_account(run / 'original.sa2s')['frames'] == 4
    assert ch.checked_run(run)[0] == 0
    assert gate.COMPARED == ('frame', 'seq', 'virtual_us', 'guest_frames', 'polls', 'bgrx_hash')


@pytest.mark.parametrize('change', ['bytes', 'mode', 'inode'])
def test_first_actor_identity_survives_successful_import(specimen, monkeypatch, change):
    root, build, run, output = specimen
    first = HS.code_inputs()
    real_exec = builtins.exec
    fired = False
    def after_actor(code, globals=None, locals=None, **kwargs):
        nonlocal fired
        value = real_exec(code, globals, locals, **kwargs)
        if globals and globals.get('__name__') == 'play_report' and not fired:
            fired = True
            p = root / 'host/player/build_record.py'
            if change == 'bytes': p.write_bytes(p.read_bytes() + b'\n')
            elif change == 'mode': p.chmod(0o764)
            else:
                old = p.with_name('held-original-build-record.py')
                p.rename(old)
                p.write_bytes(old.read_bytes())
                p.chmod(old.stat().st_mode & 0o777)
        return value
    original_hook = importlib.util.spec_from_file_location
    monkeypatch.setattr(builtins, 'exec', after_actor)
    with pytest.raises(ValueError, match='first-held code changed during import'):
        RAW_ACTORS(first)
    assert fired and importlib.util.spec_from_file_location is original_hook


def test_literal_duplicate_receipt_refuses_without_rewrite(specimen):
    root, build, run, output = specimen
    assert HS.main(['write', '--run', str(run), '--out', str(output)]) == 0
    text = output.read_text()
    output.write_text(text.replace('"replay": "metalharbor"', '"replay": "boot", "replay": "metalharbor"'))
    held = output.read_bytes()
    assert HS.main(['check', '--receipt', str(output)]) == 1
    assert output.read_bytes() == held


def test_only_fresh_checker_wall_timestamp_is_excluded(specimen, monkeypatch):
    root, build, run, output = specimen
    monkeypatch.setattr(CH, 'now', lambda: 'first fresh check time')
    assert HS.main(['write', '--run', str(run), '--out', str(output)]) == 0
    monkeypatch.setattr(CH, 'now', lambda: 'second fresh check time')
    assert HS.main(['check', '--receipt', str(output)]) == 0


@pytest.mark.parametrize('target', ['positive', 'compare-inode', 'compare-mode', 'report-inode',
                                  'reference-inode', 'receipt-inode'])
def test_first_run_and_receipt_graph_survive_actual_raw_actors(specimen, monkeypatch, target):
    root, build, run, output = specimen
    if target == 'receipt-inode':
        assert HS.main(['write', '--run', str(run), '--out', str(output)]) == 0
    path = {'compare-inode': run / 'compare.json', 'compare-mode': run / 'compare.json',
            'report-inode': run / 'sdlgpu/report.json', 'reference-inode': run / 'reference.tsv',
            'receipt-inode': output}.get(target)
    before = HS.identity(path) if path else None
    real_exec = builtins.exec
    fired = False
    def after_raw_gate(code, globals=None, locals=None, **kwargs):
        nonlocal fired
        value = real_exec(code, globals, locals, **kwargs)
        if path and globals and globals.get('__name__') == '_s5_r6_gate' and not fired:
            fired = True
            if target == 'compare-mode':
                path.chmod(0o764)
            else:
                held = path.with_name(path.name + '.held-original')
                path.rename(held)
                path.write_bytes(held.read_bytes())
                path.chmod(held.stat().st_mode & 0o7777)
        return value
    def actual_actors(first):
        ch, gate = RAW_ACTORS(first)
        # Only the same explicit tiny registered-source/count/reference policy.
        # Actual raw code, account, provenance, checker and publisher execute.
        ch.METAL_HARBOR = dict(CH.METAL_HARBOR)
        ch.BOOT_STREAM_SHA256 = 'f' * 64
        return ch, gate
    monkeypatch.setattr(HS, 'actors', actual_actors)
    monkeypatch.setattr(builtins, 'exec', after_raw_gate)
    args = ['check', '--receipt', str(output)] if target == 'receipt-inode' else ['write', '--run', str(run), '--out', str(output)]
    assert HS.main(args) == (0 if target == 'positive' else 1)
    if path:
        assert fired and before['sha256'] == HS.identity(path)['sha256']
        assert before != HS.identity(path)
    if target == 'receipt-inode':
        assert output.read_bytes() == output.with_name(output.name + '.held-original').read_bytes()
    else:
        assert output.exists() == (target == 'positive')


@pytest.mark.parametrize('change', ['payload', 'inode', 'mode'])
def test_first_receipt_survives_final_input_observation(specimen, monkeypatch, change):
    root, build, run, output = specimen
    late = {'ready': False, 'fired': False}
    def actual_actors(first):
        ch, gate = RAW_ACTORS(first)
        ch.METAL_HARBOR = dict(CH.METAL_HARBOR)
        ch.BOOT_STREAM_SHA256 = 'f' * 64
        original_inputs = ch.receipt_inputs_unchanged
        def final_inputs(held):
            value = original_inputs(held)
            if late['ready'] and not late['fired']:
                late['fired'] = True
                if change == 'payload':
                    output.write_bytes(output.read_bytes().replace(b'"R6_HOST_COMPARE_OK"', b'"forged late marker"'))
                elif change == 'mode':
                    output.chmod(0o764)
                else:
                    original = output.with_name(output.name + '.original-held')
                    output.rename(original)
                    output.write_bytes(original.read_bytes())
                    output.chmod(original.stat().st_mode & 0o7777)
            return value
        ch.receipt_inputs_unchanged = final_inputs
        return ch, gate
    monkeypatch.setattr(HS, 'actors', actual_actors)
    assert HS.main(['write', '--run', str(run), '--out', str(output)]) == 0
    first = HS.identity(output)
    prepare = HS.prepare
    def ready_after_prepare(*args, **kwargs):
        value = prepare(*args, **kwargs)
        late['ready'] = True
        return value
    monkeypatch.setattr(HS, 'prepare', ready_after_prepare)
    assert HS.main(['check', '--receipt', str(output)]) == 1
    assert late['fired'] and first != HS.identity(output)
    # Nonwriting verification preserves the other writer's changed artifact.
    if change == 'payload':
        assert json.loads(output.read_bytes())['marker'] == 'forged late marker'
    elif change == 'mode':
        assert output.stat().st_mode & 0o7777 == 0o764
    else:
        assert output.read_bytes() == output.with_name(output.name + '.original-held').read_bytes()


def test_public_pipeline_has_only_complete_producing_sources(specimen):
    root, build, run, output = specimen
    expected_sources = (
        'Documentation/decisions/2026-09-26-S5-host-sdl3-dxvk.md',
        'Documentation/replays.json',
        'Documentation/scripts/check_s5_exit.py',
        'host/d3d9gpu/build.sh',
        'host/d3d9gpu/build_before.sh',
        'host/d3d9gpu/census.cpp',
        'host/d3d9gpu/d9g.h',
        'host/d3d9gpu/d9g_clear.cpp',
        'host/d3d9gpu/d9g_clear.h',
        'host/d3d9gpu/d9g_device.cpp',
        'host/d3d9gpu/d9g_device_draw.cpp',
        'host/d3d9gpu/d9g_draw.cpp',
        'host/d3d9gpu/d9g_draw.h',
        'host/d3d9gpu/d9g_draw_state.cpp',
        'host/d3d9gpu/d9g_draw_state.h',
        'host/d3d9gpu/d9g_entry.cpp',
        'host/d3d9gpu/d9g_format.cpp',
        'host/d3d9gpu/d9g_format.h',
        'host/d3d9gpu/d9g_gpu.h',
        'host/d3d9gpu/d9g_gpu_cpu.cpp',
        'host/d3d9gpu/d9g_gpu_sdl.cpp',
        'host/d3d9gpu/d9g_ni.h',
        'host/d3d9gpu/d9g_pipeline.cpp',
        'host/d3d9gpu/d9g_pipeline.h',
        'host/d3d9gpu/d9g_props.cpp',
        'host/d3d9gpu/d9g_props.h',
        'host/d3d9gpu/d9g_report.cpp',
        'host/d3d9gpu/d9g_resources.cpp',
        'host/d3d9gpu/d9g_sampler.cpp',
        'host/d3d9gpu/d9g_sampler.h',
        'host/d3d9gpu/d9g_shader.cpp',
        'host/d3d9gpu/d9g_shader.h',
        'host/d3d9gpu/d9g_shader_abi.cpp',
        'host/d3d9gpu/d9g_shader_cache.cpp',
        'host/d3d9gpu/d9g_shader_internal.h',
        'host/d3d9gpu/d9g_shader_link.cpp',
        'host/d3d9gpu/d9g_shader_profile.cpp',
        'host/d3d9gpu/d9g_shader_profile.h',
        'host/d3d9gpu/d9g_shader_shims.cpp',
        'host/d3d9gpu/d9g_shader_vulkan.cpp',
        'host/d3d9gpu/d9g_state.h',
        'host/d3d9gpu/gen_com.py',
        'host/d3d9gpu/shader/build_translator.sh',
        'host/d3d9gpu/shader/extract_dxvk_shader.py',
        'host/d3d9gpu/state_census.sh',
        'host/d3d9gpu/test/check_census_draws.py',
        'host/d3d9gpu/test/check_draw_matrix.py',
        'host/d3d9gpu/test/check_params.py',
        'host/d3d9gpu/test/clear_cases.json',
        'host/d3d9gpu/test/clear_matrix.cpp',
        'host/d3d9gpu/test/clear_matrix.h',
        'host/d3d9gpu/test/draw_matrix.cpp',
        'host/d3d9gpu/test/extract_refs.py',
        'host/d3d9gpu/test/metal_check.py',
        'host/d3d9gpu/test/mini_json.h',
        'host/d3d9gpu/test/null_probe.cpp',
        'host/d3d9gpu/test/r1_receipt.py',
        'host/d3d9gpu/test/ref_dxvk.cpp',
        'host/d3d9gpu/test/ref_dxvk.h',
        'host/d3d9gpu/test/ref_facts.h',
        'host/d3d9gpu/test/ref_link_shims.cpp',
        'host/d3d9gpu/test/ref_sdl.c',
        'host/d3d9gpu/test/scan_projection.py',
        'host/d3d9gpu/test/shader_record.py',
        'host/d3d9gpu/test/shader_tools.py',
        'host/d3d9gpu/test/shaderc.cpp',
        'host/d3d9gpu/test/sm3_asm.py',
        'host/d3d9gpu/test/spv_canon.py',
        'host/d3d9gpu/test/spv_explained.json',
        'host/d3d9gpu/test/spv_freeze.py',
        'host/d3d9gpu/test/spv_pairs.py',
        'host/d3d9gpu/test/spv_specialize.py',
        'host/d3d9gpu/test/state_check.cpp',
        'host/d3d9gpu/test/test_d3d9gpu.cpp',
        'host/d3d9gpu/test/validation_probe/build_validation_probe.sh',
        'host/d3d9gpu/test/validation_probe/validation_probe.c',
        'host/d3d9gpu/test/validation_probe/validation_probe.json.in',
        'host/d3d9gpu/test/validation_run.py',
        'host/d3d9gpu/test/vkcap/build_vkcap.sh',
        'host/d3d9gpu/test/vkcap/vkcap.c',
        'host/d3d9gpu/test/vkcap/vkcap.json.in',
        'host/d3d9gpu/test_d3d9gpu.sh',
        'host/d3d9gpu/test_shaders.sh',
        'host/dxvk_smoke.cpp',
        'host/player/build.sh',
        'host/player/build_record.py',
        'host/player/dxvk.conf',
        'host/player/generate_expectations.py',
        'host/player/input.cpp',
        'host/player/input.h',
        'host/player/mixer.cpp',
        'host/player/mixer.h',
        'host/player/names.h',
        'host/player/pace.h',
        'host/player/play_stream.sh',
        'host/player/player.cpp',
        'host/player/player_env.sh',
        'host/player/prof.h',
        'host/player/run_player.sh',
        'host/player/sdl_mark.h',
        'host/player/sha256.cpp',
        'host/player/sha256.h',
        'host/player/smoke.sh',
        'host/player/stream.cpp',
        'host/player/stream.h',
        'host/player/synth_stream.cpp',
        'host/player/test_bad_config_contract.py',
        'host/player/test_prof.cpp',
        'host/player/test_stream.cpp',
        'host/player/test_synth.sh',
        'host/player/test_texcodec.cpp',
        'host/player/texcodec.cpp',
        'host/player/texcodec.h',
        'host/player/test_touch_overlay.cpp',
        'host/player/touch_overlay.cpp',
        'host/player/touch_overlay.h',
        'host/player/touch_overlay_shaders.py',
        'host/player/touch_overlay_spirv.h',
        'host/sa2_stream.h',
        'tools/pins.json',
        'tools/s5/compare_hosts.py',
        'tools/s5/host_compare.py',
        'tools/s5/play_report.py',
        'tools/s5/stream_dump.py',
    )
    expected = {name: CH.sha256(root / name) for name in expected_sources}
    recording = {'path': 'private/generated/s5/pushC/record/metalharbor/present-run.json',
                 'sha256': CH.sha256(root / HS.RECORDING)}
    before = {str(path): HS.identity(path) for path in run.rglob('*') if path.is_file()}
    assert HS.main(['write', '--run', str(run), '--out', str(output)]) == 0
    receipt = json.loads(output.read_bytes())
    assert receipt['inputs']['pipeline'] == expected
    assert receipt['recording'] == recording
    assert HS.RECORDING not in receipt['inputs']['pipeline']
    assert before == {str(path): HS.identity(path) for path in run.rglob('*') if path.is_file()}
    assert HS.main(['check', '--receipt', str(output)]) == 0


@pytest.mark.parametrize('change', ['mode', 'inode'])
def test_recording_physical_drift_after_validation_cannot_publish(specimen, monkeypatch, capsys, change):
    root, build, run, output = specimen
    recording = root / HS.RECORDING
    first = HS.identity(recording)
    checked = CH.checked_run
    fired = False
    def drift(directory):
        nonlocal fired
        status, doc = checked(directory)
        assert status == 0
        assert not fired
        fired = True
        if change == 'mode':
            recording.chmod(0o764)
        else:
            original = recording.with_name('present-run.original-held')
            recording.rename(original)
            recording.write_bytes(original.read_bytes())
            recording.chmod(original.stat().st_mode & 0o7777)
        return status, doc
    monkeypatch.setattr(CH, 'checked_run', drift)
    assert HS.main(['write', '--run', str(run), '--out', str(output)]) == 1
    assert fired and first['sha256'] == HS.identity(recording)['sha256']
    assert first != HS.identity(recording)
    assert 'run/document graph changed during validation' in capsys.readouterr().err
    assert not output.exists()
