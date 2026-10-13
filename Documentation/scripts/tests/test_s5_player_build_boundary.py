"""Authored CPU record conformance; no compiler, player or publication proof."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

REPO = next(p for p in Path(__file__).resolve().parents if (p / 'AGENTS.md').is_file())
CHECKER_PATH = Path(os.environ.get('SA2_TEST_PLAYER_BUILD_CHECKER',
                                 str(Path(__file__).resolve().parents[1] / 'check_s5_exit.py'))).resolve()
_spec = importlib.util.spec_from_file_location('_sa2_test_player_build_boundary', CHECKER_PATH)
S = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = S
_spec.loader.exec_module(S)
assert Path(S.__file__).resolve() == CHECKER_PATH
assert sys.modules[_spec.name] is S
CH, BR = S._player_build_providers()
assert Path(CH.__file__).resolve() == REPO / 'tools/s5/compare_hosts.py'
assert Path(BR.__file__).resolve() == REPO / 'host/player/build_record.py'
assert CH.build_record is BR
assert sys.modules['play_report'] is CH.play_report
assert sys.modules['stream_dump'] is CH.stream_dump

ROOT = '/authored-checkout'
BUILD = ROOT + '/private/build'
SOURCE_NAMES = (
    'host/player/player.cpp', 'host/player/input.cpp', 'host/player/mixer.cpp',
    'host/player/stream.cpp', 'host/player/sha256.cpp', 'host/player/texcodec.cpp',
    'host/sa2_stream.h', 'host/player/generate_expectations.py', 'tools/pins.json',
    'host/player/extra header.h',
)
DXVK_RECIPE = ('host/player/build.sh', 'host/player/dxvk.conf')
GPU_RECIPE = ('host/d3d9gpu/d9g_entry.cpp', 'host/d3d9gpu/build.sh', 'host/d3d9gpu/gen_com.py',
              'host/d3d9gpu/test/extract_refs.py', 'host/d3d9gpu/shader/build_translator.sh',
              'host/d3d9gpu/shader/extract_dxvk_shader.py')
BINARY_BYTES = b'authored player bytes\n'
HEADER_BYTES = b'authored generated header bytes\n'
SOURCE_BYTES = b'authored checkout input\n'
CONFIG_BYTES = b'authored DXVK configuration\n'
EXTERNAL_BYTES = b'authored private external header\n'
SDL_BYTES = b'authored SDL library\n'
DXVK_BYTES = b'authored DXVK library\n'
VERSION_BYTES = b'authored SDL version header\n'
REVISION_BYTES = b'authored SDL revision header\n'
# Expected public results are literal; fixture construction hashes authored bytes.
BINARY_SHA = 'c83e8d636dfeba68d3fde1b5e563e85ed9ec33136f9825aca98d82cda1ffa57e'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def identity(path, data, canonical=None):
    return S.BuildFileIdentity(path, canonical or path, sha(data), len(data), 0o644, 17, 23, ())


def text(path, data):
    return S.HeldBuildText(identity(path, data), data)


def json_text(path, doc):
    return text(path, (json.dumps(doc, separators=(',', ':')) + '\n').encode())


def escape(path):
    return path.replace('$', '$$').replace(' ', '\\ ')


class Fixture:
    def __init__(self, host='dxvk-native', root=ROOT, build=BUILD):
        self.host, self.root, self.build = host, root, build
        self.binary = 'sa2_player' if host == 'dxvk-native' else 'sa2_player_gpu'
        self.where = 'expected' if host == 'dxvk-native' else 'expected-gpu'
        names = SOURCE_NAMES + (DXVK_RECIPE if host == 'dxvk-native' else GPU_RECIPE)
        self.inputs = {}
        for name in names:
            data = CONFIG_BYTES if name.endswith('dxvk.conf') else SOURCE_BYTES
            path = root + '/' + name
            self.inputs[path] = identity(path, data)
        self.before = tuple(S.SourcePin(n, self.inputs[root + '/' + n]) for n in names)
        self.inputs[build + '/' + self.binary] = identity(build + '/' + self.binary, BINARY_BYTES)
        self.header = build + '/' + self.where + '/sa2_host_expectations.h'
        self.inputs[self.header] = identity(self.header, HEADER_BYTES)
        self.external = root + '/private/install/include/other $header.h'
        self.inputs[self.external] = identity(self.external, EXTERNAL_BYTES)
        self.version = root + '/private/install/include/SDL_version.h'
        self.revision = root + '/private/install/include/SDL_revision.h'
        self.inputs[self.version] = identity(self.version, VERSION_BYTES)
        self.inputs[self.revision] = identity(self.revision, REVISION_BYTES)
        sdl = root + '/private/install/lib/libSDL3.so.0.4'
        sdl_alias = root + '/private/install/lib/libSDL3.so.0'
        self.inputs[sdl] = identity(sdl, SDL_BYTES)
        self.inputs[sdl_alias] = identity(sdl_alias, SDL_BYTES, sdl)
        contract = {'sdl_sha256': sha(SDL_BYTES)}
        libraries = {'sdl3': {'path': sdl, 'selected_path': sdl_alias, 'sha256': sha(SDL_BYTES)}}
        if host == 'dxvk-native':
            dxvk = root + '/private/install/lib/libdxvk.so.0.3'
            dxvk_alias = root + '/private/install/lib/libdxvk.so.0'
            self.inputs[dxvk] = identity(dxvk, DXVK_BYTES)
            self.inputs[dxvk_alias] = identity(dxvk_alias, DXVK_BYTES, dxvk)
            libraries['dxvk'] = {'path': dxvk, 'selected_path': dxvk_alias, 'sha256': sha(DXVK_BYTES)}
            contract.update(dxvk_sha256=sha(DXVK_BYTES), dxvk_config_sha256=sha(CONFIG_BYTES))
        contract.update(sdl_version=3004016, sdl_revision='authored SDL revision')
        metadata_names = ['tools/pins.json', 'host/player/generate_expectations.py']
        if host == 'dxvk-native':
            metadata_names += ['host/player/dxvk.conf']
        self.expectations = {
            'contract': contract, 'libraries': libraries,
            'sources': [{'path': root + '/' + n, 'sha256': self.inputs[root + '/' + n].sha256}
                        for n in metadata_names] + [
                            {'path': self.version, 'sha256': sha(VERSION_BYTES)},
                            {'path': self.revision, 'sha256': sha(REVISION_BYTES)}],
            'header': {'path': self.header, 'sha256': sha(HEADER_BYTES)},
            'sdl3_configuration': 'patched', 'scope': 'authored only; no build or startup',
        }
        if host == 'sdl3-gpu':
            self.expectations['host'] = 'sdlgpu'
        compiled = [root + '/' + n for n in names if n.endswith(('.cpp', '.h'))]
        self.prerequisites = compiled + [self.header, self.external]
        dep_key = 'obj/' + self.binary + '/authored.d'
        self.depfiles = {dep_key: self.dep_bytes(self.prerequisites)}
        self.record = {
            'schema': 'sa2-build-record/v2', 'tree': root,
            'binaries': {self.binary: sha(BINARY_BYTES)},
            'sources': {n: self.inputs[root + '/' + n].sha256 for n in names},
            'generated': {self.where + '/sa2_host_expectations.h': sha(HEADER_BYTES)},
            'external': {self.external: sha(EXTERNAL_BYTES)}, 'depfiles': {},
            'compiler': {'command': 'authored compiler; never executed', 'version': 'authored v1',
                         'search': ['/authored/system/include', '/authored/system/include2']},
            'scope': 'authored record; no run implied',
        }
        self.report = {'files': {'player': {'path': '/proc/self/exe',
                                          'real_path': build + '/' + self.binary,
                                          'sha256': sha(BINARY_BYTES)}}}

    def dep_bytes(self, prerequisites):
        return (self.build + '/authored.o: ' + ' \\\n '.join(escape(p) for p in prerequisites) + '\n').encode()

    def files(self):
        expected = json_text(self.build + '/' + self.where + '/expectations.json', self.expectations)
        self.record['generated'][self.where + '/expectations.json'] = expected.identity.sha256
        self.record['depfiles'] = {key: sha(data) for key, data in self.depfiles.items()}
        inputs = dict(self.inputs)
        inputs[expected.identity.path] = expected.identity
        deps = tuple(text(self.build + '/' + key, data) for key, data in self.depfiles.items())
        return S.PlayerBuildFiles(json_text(self.build + '/' + self.binary + '.json', self.record),
                                  json_text(self.root + '/private/run/report.json', self.report),
                                  expected, deps, tuple(inputs.values()))

    def run(self, files=None):
        return S.player_build_evidence(self.root, self.build, self.host, self.before, files or self.files())


@pytest.mark.parametrize('host,binary,expected_record_hash,expected_paths', [
    ('dxvk-native', 'sa2_player', 'e20412d34da0cadd65a61b5487bda0adbb5ae2c370d0a99e9b7026f27b2f91de', (
        ROOT + '/host/player/build.sh', ROOT + '/host/player/dxvk.conf',
        ROOT + '/host/player/extra header.h', ROOT + '/host/player/generate_expectations.py',
        ROOT + '/host/player/input.cpp', ROOT + '/host/player/mixer.cpp', ROOT + '/host/player/player.cpp',
        ROOT + '/host/player/sha256.cpp', ROOT + '/host/player/stream.cpp', ROOT + '/host/player/texcodec.cpp',
        ROOT + '/host/sa2_stream.h', BUILD + '/expected/expectations.json',
        BUILD + '/expected/sa2_host_expectations.h', BUILD + '/obj/sa2_player/authored.d',
        BUILD + '/sa2_player', BUILD + '/sa2_player.json',
        ROOT + '/private/install/include/SDL_revision.h', ROOT + '/private/install/include/SDL_version.h',
        ROOT + '/private/install/include/other $header.h', ROOT + '/private/install/lib/libSDL3.so.0',
        ROOT + '/private/install/lib/libSDL3.so.0.4', ROOT + '/private/install/lib/libdxvk.so.0',
        ROOT + '/private/install/lib/libdxvk.so.0.3', ROOT + '/private/run/report.json', ROOT + '/tools/pins.json')),
    ('sdl3-gpu', 'sa2_player_gpu', 'c2581504842123d8c06ce67aed3e3285b7384fc1324d834b4950e5a24476d150', (
        ROOT + '/host/d3d9gpu/build.sh', ROOT + '/host/d3d9gpu/d9g_entry.cpp',
        ROOT + '/host/d3d9gpu/gen_com.py', ROOT + '/host/d3d9gpu/shader/build_translator.sh',
        ROOT + '/host/d3d9gpu/shader/extract_dxvk_shader.py', ROOT + '/host/d3d9gpu/test/extract_refs.py',
        ROOT + '/host/player/extra header.h', ROOT + '/host/player/generate_expectations.py',
        ROOT + '/host/player/input.cpp', ROOT + '/host/player/mixer.cpp', ROOT + '/host/player/player.cpp',
        ROOT + '/host/player/sha256.cpp', ROOT + '/host/player/stream.cpp', ROOT + '/host/player/texcodec.cpp',
        ROOT + '/host/sa2_stream.h', BUILD + '/expected-gpu/expectations.json',
        BUILD + '/expected-gpu/sa2_host_expectations.h', BUILD + '/obj/sa2_player_gpu/authored.d',
        BUILD + '/sa2_player_gpu', BUILD + '/sa2_player_gpu.json',
        ROOT + '/private/install/include/SDL_revision.h', ROOT + '/private/install/include/SDL_version.h',
        ROOT + '/private/install/include/other $header.h', ROOT + '/private/install/lib/libSDL3.so.0',
        ROOT + '/private/install/lib/libSDL3.so.0.4', ROOT + '/private/run/report.json', ROOT + '/tools/pins.json')),
])
def test_each_host_returns_literal_binding(host, binary, expected_record_hash, expected_paths):
    f = Fixture(host)
    assert f.run() == S.PlayerBuildBinding(host, binary, expected_record_hash, BINARY_SHA, expected_paths)


@pytest.mark.parametrize('host', ['dxvk-native', 'sdl3-gpu'])
@pytest.mark.parametrize('field', ['player.cpp', 'sa2_stream.h', 'build.sh', 'extra header.h', 'pins.json'])
def test_omitted_source_is_refused(host, field):
    f = Fixture(host)
    key = next(p for p in f.record['sources'] if p.endswith('/' + field))
    del f.record['sources'][key]
    with pytest.raises(S.Refused, match='required source|prerequisite missing'):
        f.run()


@pytest.mark.parametrize('host', ['dxvk-native', 'sdl3-gpu'])
@pytest.mark.parametrize('defect', ['missing', 'drift', 'late', 'links', 'duplicate', 'bool'])
def test_before_source_guards(host, defect):
    f = Fixture(host)
    first = f.before[0]
    if defect == 'missing':
        f.before = f.before[1:]
    elif defect == 'drift':
        f.before = (replace(first, identity=replace(first.identity, sha256='f' * 64)), *f.before[1:])
    elif defect == 'late':
        path = ROOT + '/host/player/late.h'
        f.inputs[path] = identity(path, SOURCE_BYTES)
        f.record['sources']['host/player/late.h'] = sha(SOURCE_BYTES)
    elif defect == 'links':
        f.before = (replace(first, identity=replace(first.identity, checkout_links=('host/player',))), *f.before[1:])
    elif defect == 'duplicate':
        f.before += (first,)
    else:
        f.before = (replace(first, identity=replace(first.identity, inode=True)), *f.before[1:])
    with pytest.raises(S.Refused, match='before|checkout symbolic link|identity inode'):
        f.run()


@pytest.mark.parametrize('host', ['dxvk-native', 'sdl3-gpu'])
@pytest.mark.parametrize('defect', ['missing', 'extra', 'relative', 'malformed', 'unrecorded', 'required-tu',
                                   'header', 'source-class', 'external-class', 'wrong-scope', 'utf8'])
def test_actual_depfile_parser_and_inventory_refusals(host, defect):
    f = Fixture(host)
    files = None
    key = next(iter(f.depfiles))
    if defect == 'missing':
        files = replace(f.files(), depfiles=())
    elif defect == 'extra':
        files = f.files()
        extra = text(BUILD + '/obj/' + f.binary + '/extra.d', b'/authored/x.o: /authored/x.h\n')
        files = replace(files, depfiles=files.depfiles + (extra,))
    elif defect == 'relative':
        f.depfiles[key] = f.dep_bytes(['relative/header.h', *f.prerequisites])
    elif defect == 'malformed':
        f.depfiles[key] = b'no rule here\n'
    elif defect == 'unrecorded':
        path = ROOT + '/private/install/include/unrecorded.h'
        f.inputs[path] = identity(path, EXTERNAL_BYTES)
        f.depfiles[key] = f.dep_bytes([*f.prerequisites, path])
    elif defect == 'required-tu':
        f.depfiles[key] = f.dep_bytes([p for p in f.prerequisites if not p.endswith('/player.cpp')])
    elif defect == 'header':
        f.depfiles[key] = f.dep_bytes([p for p in f.prerequisites if p != f.header])
    elif defect == 'source-class':
        path = ROOT + '/host/player/extra header.h'
        del f.record['sources']['host/player/extra header.h']
        f.record['external'][path] = sha(SOURCE_BYTES)
    elif defect == 'external-class':
        del f.record['external'][f.external]
        f.record['sources'][str(Path(f.external).relative_to(ROOT))] = sha(EXTERNAL_BYTES)
    elif defect == 'wrong-scope':
        f.depfiles['obj/other/bad.d'] = f.depfiles.pop(key)
    else:
        f.depfiles[key] = b'\xff\n'
    with pytest.raises(S.Refused, match=(
            '^player build: private/generated input misclassified as source$' if defect == 'external-class'
            else 'depfile|prerequisite|header|classification')):
        f.run(files)


@pytest.mark.parametrize('host', ['dxvk-native', 'sdl3-gpu'])
@pytest.mark.parametrize('field', ['binary', 'header', 'external', 'generated-json', 'dep-bytes', 'report-bytes',
                                  'duplicate-input', 'alias-drift', 'links', 'bool', 'float', 'bad-hash'])
def test_held_identity_and_bytes_refusals(host, field):
    f = Fixture(host)
    files = f.files()
    if field in ('binary', 'header', 'external'):
        path = {'binary': BUILD + '/' + f.binary, 'header': f.header, 'external': f.external}[field]
        files = replace(files, inputs=tuple(replace(i, sha256='e' * 64) if i.path == path else i for i in files.inputs))
    elif field == 'generated-json':
        files = replace(files, expectations=replace(files.expectations, data=b'{}\n'))
    elif field == 'dep-bytes':
        files = replace(files, depfiles=(replace(files.depfiles[0], data=b'/x: /y\n'),))
    elif field == 'report-bytes':
        files = replace(files, report=replace(files.report, data=b'{}\n'))
    elif field == 'duplicate-input':
        files = replace(files, inputs=files.inputs + (files.inputs[0],))
    elif field == 'alias-drift':
        selected = ROOT + '/private/install/lib/libSDL3.so.0'
        files = replace(files, inputs=tuple(replace(i, canonical_path=ROOT + '/private/install/lib/elsewhere')
                                          if i.path == selected else i for i in files.inputs))
    else:
        first = files.inputs[0]
        alteration = {'links': {'checkout_links': ('host/player/player.cpp',)},
                      'bool': {'size': True}, 'float': {'device': 17.0}, 'bad-hash': {'sha256': 'a' * 63}}[field]
        files = replace(files, inputs=(replace(first, **alteration), *files.inputs[1:]))
    with pytest.raises(S.Refused, match='digest|bytes|identity|input|alias|symbolic link'):
        f.run(files)


@pytest.mark.parametrize('host', ['dxvk-native', 'sdl3-gpu'])
@pytest.mark.parametrize('defect', ['reorder', 'drop', 'version-bool', 'version-float', 'revision-empty',
                                   'library-hash', 'alias-missing', 'header-hash', 'header-map',
                                   'sources-empty', 'source-missing', 'source-drift', 'source-duplicate',
                                   'version-header-missing', 'configuration', 'host'])
def test_expectations_metadata_and_contract_refusals(host, defect):
    f = Fixture(host)
    e = f.expectations
    if defect == 'reorder':
        e['contract'] = dict(reversed(list(e['contract'].items())))
    elif defect == 'drop':
        del e['contract']['sdl_sha256']
    elif defect.startswith('version-') and defect != 'version-header-missing':
        e['contract']['sdl_version'] = True if defect == 'version-bool' else 3004016.0
    elif defect == 'revision-empty':
        e['contract']['sdl_revision'] = ''
    elif defect == 'library-hash':
        e['libraries']['sdl3']['sha256'] = '9' * 64
    elif defect == 'alias-missing':
        del f.inputs[e['libraries']['sdl3']['selected_path']]
    elif defect == 'header-hash':
        e['header']['sha256'] = '8' * 64
    elif defect == 'header-map':
        del f.record['generated'][f.where + '/sa2_host_expectations.h']
    elif defect == 'sources-empty':
        e['sources'] = []
    elif defect == 'source-missing':
        del f.inputs[f.revision]
    elif defect == 'source-drift':
        f.inputs[f.revision] = replace(f.inputs[f.revision], sha256='7' * 64)
    elif defect == 'source-duplicate':
        e['sources'].append(copy.deepcopy(e['sources'][0]))
    elif defect == 'version-header-missing':
        e['sources'] = [s for s in e['sources'] if s['path'] != f.version]
    elif defect == 'configuration':
        e['sdl3_configuration'] = 'invented'
    else:
        e['host'] = 'other'
    with pytest.raises(S.Refused, match=(
            '^player build: prerequisite missing/wrong classification$' if defect == 'header-map'
            else 'contract|library|input|header|sources|source|SDL|host|generated')):
        f.run()


@pytest.mark.parametrize('host', ['dxvk-native', 'sdl3-gpu'])
@pytest.mark.parametrize('defect', ['schema', 'tree', 'second-binary', 'report-hash', 'report-path', 'proc-label',
                                   'compiler-command', 'compiler-version', 'compiler-search', 'compiler-relative',
                                   'record-path', 'source-key', 'external-key'])
def test_record_report_and_compiler_refusals(host, defect):
    f = Fixture(host)
    files = None
    if defect == 'schema':
        f.record['schema'] = 'sa2-build-record/v1'
    elif defect == 'tree':
        f.record['tree'] = '/another-checkout'
    elif defect == 'second-binary':
        f.record['binaries']['other'] = sha(BINARY_BYTES)
    elif defect == 'report-hash':
        f.report['files']['player']['sha256'] = '6' * 64
    elif defect == 'report-path':
        f.report['files']['player']['real_path'] = '/replaced/player'
    elif defect == 'proc-label':
        f.report['files']['player']['path'] = '/unobserved/program'
    elif defect.startswith('compiler-'):
        part = defect.removeprefix('compiler-')
        if part == 'relative':
            f.record['compiler']['search'] = ['relative/include']
        else:
            f.record['compiler'][part] = None
    elif defect == 'record-path':
        files = f.files()
        files = replace(files, record=replace(files.record, identity=replace(files.record.identity,
                        path=BUILD + '/wrong.json', canonical_path=BUILD + '/wrong.json')))
    elif defect == 'source-key':
        f.record['sources']['../elsewhere'] = sha(SOURCE_BYTES)
    else:
        f.record['external']['relative/header.h'] = sha(EXTERNAL_BYTES)
    with pytest.raises(S.Refused, match='schema|sole binary|report|compiler|record path|canonical path'):
        f.run(files)


def test_host_binding_is_selected_by_caller():
    f = Fixture('sdl3-gpu')
    f.host = 'dxvk-native'
    with pytest.raises(S.Refused, match='sole binary'):
        f.run()


def test_dxvk_cannot_contain_gpu_source():
    f = Fixture()
    path = ROOT + '/host/d3d9gpu/d9g_format.cpp'
    f.inputs[path] = identity(path, SOURCE_BYTES)
    f.before += (S.SourcePin('host/d3d9gpu/d9g_format.cpp', f.inputs[path]),)
    f.record['sources']['host/d3d9gpu/d9g_format.cpp'] = sha(SOURCE_BYTES)
    with pytest.raises(S.Refused, match='second-host'):
        f.run()


def test_before_broad_snapshot_need_not_all_compile():
    f = Fixture()
    path = ROOT + '/host/player/uncompiled-tool.py'
    f.before += (S.SourcePin('host/player/uncompiled-tool.py', identity(path, SOURCE_BYTES)),)
    got = f.run()
    assert (got.host, got.binary, path in got.input_paths) == ('dxvk-native', 'sa2_player', False)


def test_held_parser_does_not_reopen_named_paths(tmp_path):
    f = Fixture(root=str(tmp_path / 'checkout'), build=str(tmp_path / 'checkout/private/build'))
    files = f.files()
    path = Path(files.report.identity.path)
    path.parent.mkdir(parents=True)
    path.write_bytes(b'changed after capture; deliberately invalid JSON')
    got = f.run(files)
    assert (got.host, got.binary, got.binary_sha256) == ('dxvk-native', 'sa2_player', BINARY_SHA)
    assert path.read_bytes() == b'changed after capture; deliberately invalid JSON'


@pytest.mark.parametrize('field', ['record', 'report', 'expectations'])
@pytest.mark.parametrize('bad', [b'{"x":1,"x":2}\n', b'{"x":NaN}\n', b'[]\n', b'\xff\n'])
def test_strict_json_bytes_refuse_before_semantics(field, bad):
    f = Fixture()
    files = replace(f.files(), **{field: text(getattr(f.files(), field).identity.path, bad)})
    with pytest.raises(S.Refused, match='JSON object'):
        f.run(files)


@pytest.mark.parametrize('root,build,host', [('/x', '/', 'dxvk-native'),
                                          ('relative', BUILD, 'dxvk-native'),
                                          (ROOT, BUILD, 'invented'),
                                          (ROOT, ROOT + '/private/../build', 'dxvk-native')])
def test_invalid_roots_and_host_refuse(root, build, host):
    f = Fixture()
    with pytest.raises(S.Refused, match='checkout|canonical path|unknown host'):
        S.player_build_evidence(root, build, host, f.before, f.files())
