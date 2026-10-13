"""Test actual supplemental pin dispatch with Python-authored Git objects/index.

Only source authority is replaced to approve fixture commits. Raw source/output
checks are real. No Git mutation command runs. These tests do not prove approved
upstream identity, builds, ABI, or runtime use.
"""
from __future__ import annotations
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
import zlib
import pytest

REPO = Path(os.environ.get('SA2_VALIDATION_TEST_REPO', Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(REPO / 'Documentation/scripts'))
sys.path.insert(0, str(REPO / 'tools/s5'))
spec = importlib.util.spec_from_file_location('supplemental_pin_checker', REPO / 'Documentation/scripts/check_pins.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
helper = checker.validation_tools
KEYS = ('validation-350-vulkan-headers', 'validation-350-vulkan-utility-libraries',
        'validation-350-spirv-headers', 'validation-350-spirv-tools',
        'validation-350-vulkan-validation-layers')
VVL = KEYS[-1]
PREFIX = 'private/s5-tools/validation-350/runs/fixture-01/install'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def source_repository(repo, files):
    """Write raw Git objects and a valid v2 index without executing Git."""
    gd = repo / '.git'
    (gd / 'objects').mkdir(parents=True)
    (gd / 'refs').mkdir()
    (gd / 'info').mkdir()
    (gd / 'config').write_text('[core]\n repositoryformatversion = 0\n bare = false\n filemode = true\n')

    def obj(kind, body):
        raw = kind.encode() + b' ' + str(len(body)).encode() + b'\0' + body
        oid = hashlib.sha1(raw).hexdigest()
        dest = gd / 'objects' / oid[:2] / oid[2:]
        dest.parent.mkdir(exist_ok=True)
        dest.write_bytes(zlib.compress(raw))
        return bytes.fromhex(oid)

    records, entries = {}, []
    for name, (mode, data) in sorted(files.items()):
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if mode == 0o120000:
            path.symlink_to(data.decode())
        elif mode == 0o160000:
            path.mkdir()
        else:
            path.write_bytes(data)
            path.chmod(mode & 0o777)
        oid = bytes.fromhex('1' * 40) if mode == 0o160000 else obj('blob', data)
        records[name] = mode, oid
        st = path.lstat()
        fields = (int(st.st_ctime), st.st_ctime_ns % 10**9, int(st.st_mtime),
                  st.st_mtime_ns % 10**9, st.st_dev, st.st_ino, mode, st.st_uid, st.st_gid, st.st_size)
        entry = struct.pack('>10I', *(x & 0xffffffff for x in fields)) + oid
        entry += struct.pack('>H', len(name.encode())) + name.encode() + b'\0'
        entries.append(entry + b'\0' * (-len(entry) % 8))

    def tree(at):
        direct = {}
        for path, record in records.items():
            if path.startswith(at):
                head, sep, _ = path[len(at):].partition('/')
                direct[head] = (0o40000, None) if sep else record
        body = bytearray()
        for name, (mode, oid) in sorted(direct.items(), key=lambda p: p[0] + ('/' if p[1][0] == 0o40000 else '')):
            oid = tree(at + name + '/') if mode == 0o40000 else oid
            body.extend(f'{mode:o} {name}'.encode() + b'\0' + oid)
        return obj('tree', bytes(body))

    tid = tree('')
    cid = obj('commit', b'tree ' + tid.hex().encode() + b'\nauthor Fixture <fixture@example.invalid> 12345 +0000\ncommitter Fixture <fixture@example.invalid> 12345 +0000\n\n' + repo.name.encode() + b' fixture\n')
    (gd / 'HEAD').write_text(cid.hex() + '\n')
    index = b'DIRC' + struct.pack('>II', 2, len(entries)) + b''.join(entries)
    (gd / 'index').write_bytes(index + hashlib.sha1(index).digest())
    return {'commit': cid.hex(), 'tree': tid.hex()}


def installed_entry(root, row, source):
    key = row['key']
    entry = {**source, 'path': f"private/s5-tools/validation-350/sources/{row['name']}",
             'version': row['ref'], 'prefix': PREFIX, 'installed_files': {}, 'roles': {}}

    def install(path, data=b'authored stage output\n'):
        dest = root / PREFIX / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        entry['installed_files'][path] = sha(data)
        return path

    def role(name, path, data=b'authored stage output\n'):
        entry['roles'][name] = install(path, data)

    if key == KEYS[0]:
        role('vulkan_header', 'include/vulkan/vulkan_core.h', b'#define VK_HEADER_VERSION 350\n')
        role('package_config', 'share/cmake/VulkanHeaders/VulkanHeadersConfig.cmake')
    elif key == KEYS[1]:
        role('utility_header', 'include/vulkan/utility/vk_safe_struct.hpp')
        role('layer_settings_library', 'lib/libVulkanLayerSettings.a')
        role('safe_struct_library', 'lib/libVulkanSafeStruct.a')
        role('package_config', 'lib/cmake/VulkanUtilityLibraries/VulkanUtilityLibrariesConfig.cmake')
    elif key == KEYS[2]:
        role('spirv_header', 'include/spirv/unified1/spirv.h')
        role('package_config', 'share/cmake/SPIRV-Headers/SPIRV-HeadersConfig.cmake')
    elif key == KEYS[3]:
        role('spirv_tools_library', 'lib/libSPIRV-Tools.a')
        role('optimizer_library', 'lib/libSPIRV-Tools-opt.a')
        role('package_config', 'lib/cmake/SPIRV-Tools/SPIRV-ToolsConfig.cmake')
        role('optimizer_package_config', 'lib/cmake/SPIRV-Tools-opt/SPIRV-Tools-optConfig.cmake')
        for alias, name in (('binary', 'spirv-val'), ('disassembler', 'spirv-dis'), ('assembler', 'spirv-as'), ('optimizer', 'spirv-opt')):
            rel = install('bin/' + name, b'fixture executable ' + name.encode() + b'\n')
            entry[alias], entry[alias + '_sha256'] = PREFIX + '/' + rel, entry['installed_files'][rel]
    else:
        rel = install('lib/libVkLayer_khronos_validation.so', b'authored validation library\n')
        entry['library'], entry['library_sha256'] = PREFIX + '/' + rel, entry['installed_files'][rel]
        document = {'file_format_version': '1.2.0', 'layer': {'name': 'VK_LAYER_KHRONOS_validation', 'type': 'GLOBAL',
                    'api_version': '1.4.350', 'implementation_version': '1', 'description': 'authored fixture',
                    'library_path': 'libVkLayer_khronos_validation.so'}}
        rel = install('share/vulkan/explicit_layer.d/VkLayer_khronos_validation.json', json.dumps(document).encode())
        entry['manifest'], entry['manifest_sha256'] = PREFIX + '/' + rel, entry['installed_files'][rel]
    return entry


def approve_fixture_rows(root, rows, monkeypatch):
    # Approve only authored fixture commits; no reader/adapter/output guard is mocked.
    path = root / 'tools/s5/validation-sources-350.json'
    doc = json.loads((REPO / 'tools/s5/validation-sources-350.json').read_text())
    doc['sources'] = rows
    data = (json.dumps(doc, indent=2) + '\n').encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    monkeypatch.setattr(helper, 'SOURCE_POLICY', tuple(rows))
    monkeypatch.setattr(helper, 'SOURCE_MANIFEST_SHA256', sha(data))


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / 'project'
    root.mkdir()
    rows = json.loads((REPO / 'tools/s5/validation-sources-350.json').read_text())['sources']
    tools = {}
    for row in rows:
        source = source_repository(root / f"private/s5-tools/validation-350/sources/{row['name']}",
                                   {'source.cpp': (0o100644, b'int answer = 1;\n')})
        row['commit'] = source['commit']
        tools[row['key']] = installed_entry(root, row, source)
    approve_fixture_rows(root, rows, monkeypatch)
    return root, tools


def run(project, key=None, sources_only=False):
    root, tools = project
    path = root / 'tools/pins.json'
    path.write_text(json.dumps({'schema': 'sa2-recomp-pins/v1', 'tools': tools}))
    return checker.check(root, path, [key] if key else list(tools), sources_only)


def rejected(project, key=VVL, sources_only=False):
    lines, ok = run(project, key, sources_only)
    assert ok is False, '\n'.join(lines)
    assert any(line.startswith(key + ': FAIL ') for line in lines), lines
    assert lines[-1] == f'tool pins FAIL: {key}; 0 tools verified, 0 unverifiable (none)'
    return '\n'.join(lines)


@pytest.mark.parametrize('sources_only', [False, True])
def test_all_five_receive_real_verification(project, sources_only):
    lines, ok = run(project, sources_only=sources_only)
    assert ok is True, '\n'.join(lines)
    assert lines[-1] == 'tool pins OK: 5 tools verified, 0 unverifiable (none)'
    for key in KEYS:
        assert any(line.startswith(key + ': OK ') for line in lines), lines


@pytest.mark.parametrize('key', KEYS)
@pytest.mark.parametrize('sources_only', [False, True])
@pytest.mark.parametrize('field,value', [('commit', 'f'*40), ('tree', 'e'*40), ('version', 'wrong'),
                                         ('path', 'private/s5-tools/validation-350/sources/alias')])
def test_each_key_refuses_wrong_identity(project, key, sources_only, field, value):
    project[1][key][field] = value
    rejected(project, key, sources_only)


@pytest.mark.parametrize('key', KEYS)
@pytest.mark.parametrize('sources_only', [False, True])
@pytest.mark.parametrize('field', ['commit', 'tree', 'version', 'path'])
def test_each_key_requires_source_fields(project, key, sources_only, field):
    del project[1][key][field]
    rejected(project, key, sources_only)


@pytest.mark.parametrize('key', KEYS)
@pytest.mark.parametrize('sources_only', [False, True])
def test_supplemental_key_cannot_bypass_proof(project, key, sources_only):
    project[1][key] = {'unpinned': True, 'reason': 'attempted bypass'}
    assert 'supplemental validation source must be pinned' in rejected(project, key, sources_only)


@pytest.mark.parametrize('sources_only', [False, True])
@pytest.mark.parametrize('mutation', ['raw_bytes', 'executable_mode', 'untracked', 'ignored', 'assume_unchanged',
                                    'skip_worktree', 'index_blob', 'object_corruption', 'source_directory_alias'])
def test_source_proof_checks_actual_files_and_index(project, sources_only, mutation):
    root, tools = project
    checkout = root / tools[VVL]['path']
    path = checkout / 'source.cpp'
    if mutation == 'raw_bytes':
        path.write_bytes(b'int answer = 9;\n')
    elif mutation == 'executable_mode':
        path.chmod(0o755)
    elif mutation in ('untracked', 'ignored'):
        name = 'extra.cpp'
        if mutation == 'ignored':
            (checkout / '.git/info/exclude').write_text(name + '\n')
        (checkout / name).write_bytes(b'extra\n')
    elif mutation in ('assume_unchanged', 'skip_worktree', 'index_blob'):
        data = bytearray((checkout / '.git/index').read_bytes()[:-20])
        if mutation == 'assume_unchanged':
            data[72:74] = struct.pack('>H', struct.unpack('>H', data[72:74])[0] | 0x8000)
        elif mutation == 'skip_worktree':
            data[4:8] = struct.pack('>I', 3)
            data[72:74] = struct.pack('>H', struct.unpack('>H', data[72:74])[0] | 0x4000)
            data = data[:74] + struct.pack('>H', 0x4000) + data[74:-2]
        else:
            data[52:72] = bytes.fromhex('f'*40)
        (checkout / '.git/index').write_bytes(bytes(data) + hashlib.sha1(data).digest())
    elif mutation == 'object_corruption':
        oid = tools[VVL]['commit']
        (checkout / '.git/objects' / oid[:2] / oid[2:]).write_bytes(zlib.compress(b'commit 3\0bad'))
    else:
        target = root / 'source-alias-target'
        checkout.rename(target)
        checkout.symlink_to(target, target_is_directory=True)
    rejected(project, sources_only=sources_only)


def replace_source(project, monkeypatch, files):
    root, tools = project
    checkout = root / tools[VVL]['path']
    checkout.rename(root / 'retained-old-source')
    source = source_repository(checkout, files)
    tools[VVL].update(source)
    rows = copy.deepcopy(list(helper.SOURCE_POLICY))
    next(row for row in rows if row['key'] == VVL)['commit'] = source['commit']
    approve_fixture_rows(root, rows, monkeypatch)
    return checkout


@pytest.mark.parametrize('sources_only', [False, True])
def test_clean_filter_cannot_hide_changed_source(project, monkeypatch, sources_only):
    root, tools = project
    checkout = replace_source(project, monkeypatch, {'.gitattributes': (0o100644, b'source.cpp filter=hide\n'),
                                                    'source.cpp': (0o100644, b'int answer = 1;\n')})
    canonical = root / 'canonical.cpp'
    canonical.write_bytes(b'int answer = 1;\n')
    with (checkout / '.git/config').open('a') as config:
        config.write(f'[filter "hide"]\n clean = cat {canonical}\n')
    (checkout / 'source.cpp').write_bytes(b'int answer = 9;\n')
    old_detail = checker.verify_checkout_sources(root, tools[VVL])
    assert 'working tree clean, nothing ignored, no index entry skipped' in old_detail
    rejected(project, sources_only=sources_only)


@pytest.mark.parametrize('sources_only', [False, True])
@pytest.mark.parametrize('mode,content', [(0o120000, b'../../../../../../external.cpp'), (0o120000, b'.git/config'), (0o160000, b'')])
def test_escaping_source_links_and_gitlinks_fail(project, monkeypatch, sources_only, mode, content):
    (project[0] / 'external.cpp').write_bytes(b'outside\n')
    replace_source(project, monkeypatch, {'source.cpp': (mode, content)})
    rejected(project, sources_only=sources_only)


@pytest.mark.parametrize('sources_only', [False, True])
def test_interior_source_link_and_executable_are_valid(project, monkeypatch, sources_only):
    replace_source(project, monkeypatch, {'include/source.cpp': (0o100755, b'int answer = 1;\n'),
                                       'source.cpp': (0o120000, b'include/source.cpp')})
    lines, ok = run(project, VVL, sources_only)
    assert ok is True, '\n'.join(lines)
    assert lines[-1] == 'tool pins OK: 1 tools verified, 0 unverifiable (none)'


@pytest.mark.parametrize('key', KEYS)
@pytest.mark.parametrize('mutation', ['missing_file', 'changed_file', 'missing_map', 'empty_map', 'bad_map', 'non_hash',
                                    'bad_prefix', 'escaping_map_path', 'directory_alias', 'escaped_file_link', 'nested_directory_link'])
def test_installed_map_and_exact_bytes_are_required(project, key, mutation):
    root, tools = project
    entry = tools[key]
    rel = next(iter(entry['installed_files']))
    if mutation == 'missing_file':
        (root / PREFIX / rel).unlink()
    elif mutation == 'changed_file':
        (root / PREFIX / rel).write_bytes(b'changed output\n')
    elif mutation == 'missing_map':
        del entry['installed_files']
    elif mutation == 'empty_map':
        entry['installed_files'] = {}
    elif mutation == 'bad_map':
        entry['installed_files'] = []
    elif mutation == 'non_hash':
        entry['installed_files'][rel] = 'not a digest'
    elif mutation == 'bad_prefix':
        entry['prefix'] = 'private/generated/alias/install'
    elif mutation == 'escaping_map_path':
        entry['installed_files']['../external'] = 'f'*64
    elif mutation == 'directory_alias':
        install = root / PREFIX
        target = root / 'aliased-install'
        install.rename(target)
        install.symlink_to(target, target_is_directory=True)
    elif mutation == 'escaped_file_link':
        path = root / PREFIX / rel
        target = root / 'external-output'
        target.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(target)
    else:
        parent = (root / PREFIX / rel).parent
        target = root / PREFIX / 'aliased-directory'
        parent.rename(target)
        parent.symlink_to(target, target_is_directory=True)
    rejected(project, key)


@pytest.mark.parametrize('key', KEYS)
def test_source_only_needs_no_output_fields(project, key):
    entry = project[1][key]
    for field in ('prefix', 'installed_files', 'roles', 'binary', 'disassembler', 'assembler', 'optimizer', 'library', 'manifest'):
        entry.pop(field, None)
        entry.pop(field + '_sha256', None)
    lines, ok = run(project, key, True)
    assert ok is True, '\n'.join(lines)
    assert lines[-1] == 'tool pins OK: 1 tools verified, 0 unverifiable (none)'
    rejected(project, key)


@pytest.mark.parametrize('key', KEYS[:-1])
@pytest.mark.parametrize('mutation', ['missing_roles', 'bad_roles', 'missing_role', 'outside_map', 'wrong_role_in_map'])
def test_consumed_stage_roles_are_required(project, key, mutation):
    root, tools = project
    entry = tools[key]
    if mutation == 'missing_roles':
        del entry['roles']
    elif mutation == 'bad_roles':
        entry['roles'] = []
    elif mutation == 'missing_role':
        entry['roles'].pop(next(iter(entry['roles'])))
    elif mutation == 'outside_map':
        entry['roles'][next(iter(entry['roles']))] = 'not-in-map'
    else:
        role = next(iter(entry['roles']))
        rel = 'misc/unrelated-output'
        data = (root / PREFIX / entry['roles'][role]).read_bytes()
        (root / PREFIX / rel).parent.mkdir(exist_ok=True)
        (root / PREFIX / rel).write_bytes(data)
        entry['installed_files'][rel] = sha(data)
        entry['roles'][role] = rel
    rejected(project, key)


@pytest.mark.parametrize('key,alias', [(KEYS[3], 'binary'), (KEYS[3], 'disassembler'), (KEYS[3], 'assembler'),
                                     (KEYS[3], 'optimizer'), (VVL, 'library'), (VVL, 'manifest')])
@pytest.mark.parametrize('mutation', ['missing_alias', 'missing_hash', 'hash_disagreement', 'outside_prefix', 'outside_map', 'wrong_name_in_map'])
def test_alias_identity_is_required(project, key, alias, mutation):
    root, tools = project
    entry = tools[key]
    if mutation == 'missing_alias':
        del entry[alias]
    elif mutation == 'missing_hash':
        del entry[alias + '_sha256']
    elif mutation == 'hash_disagreement':
        entry[alias + '_sha256'] = 'f'*64
    elif mutation == 'outside_prefix':
        entry[alias] = 'private/s5-tools/other/output'
    elif mutation == 'outside_map':
        entry[alias] = PREFIX + '/other-output'
        path = root / entry[alias]
        path.write_bytes(b'outside map\n')
        entry[alias + '_sha256'] = sha(path.read_bytes())
    else:
        data = (root / entry[alias]).read_bytes()
        rel = 'other-output'
        (root / PREFIX / rel).write_bytes(data)
        entry['installed_files'][rel] = sha(data)
        entry[alias], entry[alias + '_sha256'] = PREFIX + '/' + rel, sha(data)
    rejected(project, key)


@pytest.mark.parametrize('field,value', [('library_path', 'absolute-other'),
                                       ('library_path', 'relative-other'),
                                       ('library_path', 'wrong-library.so'), ('name', 'VK_LAYER_wrong'),
                                       ('api_version', '1.4.309')])
def test_manifest_identity_cannot_be_self_consistent_but_wrong(project, field, value):
    root, tools = project
    entry = tools[VVL]
    path = root / entry['manifest']
    doc = json.loads(path.read_text())
    if value in ('absolute-other', 'relative-other'):
        other = root / PREFIX / 'lib/other/libVkLayer_khronos_validation.so'
        other.parent.mkdir()
        other.write_bytes((root / entry['library']).read_bytes())
        entry['installed_files'][str(other.relative_to(root / PREFIX))] = sha(other.read_bytes())
        value = str(other) if value == 'absolute-other' else os.path.relpath(other, path.parent)
    doc['layer'][field] = value
    data = json.dumps(doc).encode()
    path.write_bytes(data)
    entry['manifest_sha256'] = sha(data)
    entry['installed_files'][str(path.relative_to(root / PREFIX))] = sha(data)
    rejected(project)


def test_installed_header_requires_revision_350(project):
    root, tools = project
    entry = tools[KEYS[0]]
    rel = entry['roles']['vulkan_header']
    data = b'#define VK_HEADER_VERSION 309\n'
    (root / PREFIX / rel).write_bytes(data)
    entry['installed_files'][rel] = sha(data)
    rejected(project, KEYS[0])


def test_unknown_and_legacy_unpinned_accounting_remain(project):
    project[1].clear()
    project[1].update({'mystery': {'version': '1'}, 'other': {'unpinned': True, 'reason': 'explicit exception'}})
    lines, ok = run(project)
    assert ok is True
    assert lines == ['mystery: UNVERIFIABLE no verifier for this tool in check_pins.py',
                     'other: UNPINNED explicit exception',
                     'tool pins OK: 0 tools verified, 1 unverifiable (mystery), 1 unpinned (other)']
    lines, ok = run(project, 'mystery', True)
    assert ok is False
    assert lines[-1] == 'tool pins FAIL: mystery; 0 tools verified, 0 unverifiable (none)'
