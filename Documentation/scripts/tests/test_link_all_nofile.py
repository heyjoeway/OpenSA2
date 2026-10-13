"""Tiny extracted-production CPU owner. No compiler, linker or game is launched."""
import ast
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

REPO = Path(os.environ.get('SA2_LINK_FD_REPO_ROOT', str(Path(__file__).parents[3])))
SOURCE = Path(__file__).parents[3] / 'tools/s3/whole_image/link_all.py'
SOURCE_SHA = 'd9fd570d99cf5da9d29cf5c86b4f5cb35f044feb1392e2c199e51ae80de82794'


def extract(path, names, scope):
    tree = ast.parse(path.read_bytes())
    selected = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in selected} == set(names)
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), 'exec'), scope)
    return scope


class Limits:
    RLIM_INFINITY = -1
    RLIMIT_NOFILE = 7
    RUSAGE_CHILDREN = -1

    def __init__(self, soft=64, hard=1024, failure=None):
        self.values = (soft, hard)
        self.failure = failure
        self.get_calls = 0
        self.set_calls = []

    def getrusage(self, kind):
        assert kind == self.RUSAGE_CHILDREN
        return SimpleNamespace(ru_maxrss=1024)

    def getrlimit(self, kind):
        assert kind == self.RLIMIT_NOFILE
        self.get_calls += 1
        if self.failure == 'get': raise OSError('authored getrlimit failure')
        if self.failure == 'readback' and self.get_calls == 2: return (128, self.values[1])
        if self.failure == 'second-read' and self.get_calls == 2: raise OSError('authored second read failure')
        return self.values

    def setrlimit(self, kind, values):
        assert kind == self.RLIMIT_NOFILE
        self.set_calls.append(values)
        if self.failure == 'set': raise PermissionError('authored setrlimit failure')
        self.values = values


class Child:
    TimeoutExpired = subprocess.TimeoutExpired

    def __init__(self, timeout=False):
        self.calls = []
        self.timeout = timeout

    def run(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        if self.timeout: raise self.TimeoutExpired(cmd, kwargs['timeout'])
        return SimpleNamespace(returncode=0, stdout='ordinary child output', stderr='')


def runner(limits=None, child=None):
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == SOURCE_SHA
    limits = Limits() if limits is None else limits
    child = Child() if child is None else child
    scope = extract(SOURCE, {'_link_nofile', 'run'}, {
        'Path': Path, 'resource': limits, 'subprocess': child, 'time': time})
    return scope, limits, child


def test_low_soft_high_hard_counts_inputs_not_output_and_preserves_run_options(tmp_path):
    scope, limits, child = runner()
    cmd = ['/pinned/tools/zig', 'c++', 'a.o', 'b.o', '-o', 'output.o']
    result = scope['run'](cmd, cwd=tmp_path, timeout=19)
    assert result['rc'] == 0
    assert result['link_nofile'] == {
        'object_inputs': 2, 'headroom': 256, 'needed': 258,
        'soft_before': 64, 'soft_effective': 258, 'hard': 1024, 'hard_effective': 1024,
        'infinity': -1, 'raised': True, 'error': None}
    assert limits.set_calls == [(258, 1024)]
    assert child.calls == [(cmd, {'capture_output': True, 'text': True, 'timeout': 19, 'cwd': str(tmp_path)})]


@pytest.mark.parametrize('soft,hard', [(1024, 2048), (-1, -1)])
def test_already_high_or_infinite_is_preserved(soft, hard):
    scope, limits, child = runner(Limits(soft, hard))
    result = scope['run'](['zig', 'cc', 'a.o', '-o', 'binary'])
    assert result['rc'] == 0
    assert result['link_nofile']['soft_before'] == result['link_nofile']['soft_effective'] == soft
    assert result['link_nofile']['hard'] == result['link_nofile']['hard_effective'] == hard
    assert result['link_nofile']['raised'] is False
    assert limits.set_calls == [] and len(child.calls) == 1


def test_low_soft_with_infinite_hard_preserves_infinite_hard():
    scope, limits, child = runner(Limits(64, -1))
    result = scope['run'](['zig', 'cc', 'a.o'])
    assert result['rc'] == 0
    assert limits.set_calls == [(257, -1)]
    assert result['link_nofile']['soft_effective'] == 257
    assert result['link_nofile']['hard_effective'] == -1


@pytest.mark.parametrize('failure,message', [
    ('hard', 'unchanged hard limit is 256'),
    ('get', 'OSError: authored getrlimit failure'),
    ('set', 'PermissionError: authored setrlimit failure'),
    ('readback', 'NOFILE readback'),
    ('second-read', 'OSError: authored second read failure')])
def test_limit_refusal_returns_normal_nonzero_result_without_child(failure, message):
    limits = Limits(64, 256 if failure == 'hard' else 1024,
                    None if failure == 'hard' else failure)
    scope, limits, child = runner(limits)
    result = scope['run'](['zig', 'cc', 'a.o'])
    assert result['rc'] == -1 and result['stdout'] == ''
    assert message in result['stderr_tail']
    assert 'seconds' in result and 'peak_child_rss_mb' in result and 'peak_rose_mb' in result
    assert result['link_nofile']['needed'] == 257
    assert child.calls == []
    if failure in ('hard', 'get'): assert limits.set_calls == []
    else: assert limits.set_calls == [(257, 1024)]
    if failure == 'second-read':
        assert result['link_nofile']['soft_effective'] is None
        assert result['link_nofile']['hard_effective'] is None
        assert result['link_nofile']['raised'] is True
        assert limits.get_calls == 2


@pytest.mark.parametrize('cmd', [
    ['zig', 'cc', '-c', 'a.c', '-o', 'output.o'],
    ['zig', 'cc', '--compile', 'a.o', '-o', 'output.o'],
    ['zig', 'c++', '-S', 'a.o'], ['zig', 'cc', '-E', 'a.o'],
    ['zig', 'cc', '-fsyntax-only', 'a.o'], ['clang', 'cc', 'a.o'],
    ['zig', 'build', 'a.o'], ['zig', 'cc', 'a.c', '-o', 'output.o']])
def test_compile_nonzig_and_source_only_commands_keep_old_shape(cmd):
    scope, limits, child = runner()
    result = scope['run'](cmd)
    assert set(result) == {'rc', 'seconds', 'peak_child_rss_mb', 'peak_rose_mb', 'stdout', 'stderr_tail'}
    assert result['rc'] == 0 and result['stdout'] == 'ordinary child output'
    assert limits.get_calls == 0 and limits.set_calls == []
    assert child.calls[0][0] == cmd


def test_timeout_retains_existing_shape_and_link_limit_record():
    scope, limits, child = runner(child=Child(timeout=True))
    result = scope['run'](['zig', 'cc', 'a.o'], timeout=3)
    assert result['rc'] == -1 and result['stdout'] == ''
    assert result['stderr_tail'] == 'timeout after 3 s'
    assert result['link_nofile']['soft_effective'] == 257
    assert len(child.calls) == 1


def test_real_phase_link_replaces_old_success_and_build_ok_refuses(tmp_path):
    scope, limits, child = runner(Limits(64, 256))
    out = tmp_path / 'link'
    obj = tmp_path / 'build' / 'tag'
    obj.mkdir(parents=True)
    for name in ['ir_a.o', 'ir_b.o']: (obj / name).write_bytes(b'authored object')
    bdir = out / 'mains/toy/obj'; bdir.mkdir(parents=True)
    for name in ['function_table_whole.o', 'main.o']: (bdir / name).write_bytes(b'authored object')
    binary = out / 'mains/toy/sa2_main'; binary.write_bytes(b'previous authored binary')
    fake_common = SimpleNamespace(ROOT=tmp_path, BUILD=tmp_path/'build',
        sha256=lambda p: hashlib.sha256(p.read_bytes()).hexdigest())
    link = extract(REPO/'tools/s4/link_s4.py', {'phase_link'}, {
        'Path':Path,'OUT':out,'C':fake_common,'S':SimpleNamespace(ZIG='zig'),
        'RUNTIME_C':[],'RUNTIME_CXX':[],'TARGET':[],'OPT':'-O2','PRODUCTION_WRAPS':[],
        'L':SimpleNamespace(run=scope['run']),'time':time})
    state = {'compile_ok':True,'link':{'rc':0,'sha256':'old-success'}}
    link['phase_link'](state, 'toy', 'tag', [])
    assert state['link']['rc'] == -1
    assert state['link']['link_nofile']['object_inputs'] == 4
    assert state['link']['link_nofile']['needed'] == 260
    assert 'sha256' not in state['link'] and 'binary' not in state['link']
    assert child.calls == [] and binary.read_bytes() == b'previous authored binary'
    (out/'main.toy.json').write_text(json.dumps(state)+'\n')
    loop = extract(REPO/'tools/s4/loop_run.py', {'build_ok'}, {'LINKDIR':out,'json':json})
    assert loop['build_ok']('toy') is False


def test_actual_benign_python_child_inherits_raised_soft_and_unchanged_hard(tmp_path):
    original = resource.getrlimit(resource.RLIMIT_NOFILE)
    needed = 556
    assert original[1] == resource.RLIM_INFINITY or original[1] >= needed

    class PythonChild:
        TimeoutExpired = subprocess.TimeoutExpired
        def run(self, cmd, **kwargs):
            # Authored routing replaces Zig with this benign Python child only.
            assert cmd[0:2] == ['zig', 'c++']
            code = 'import json,resource; print(json.dumps(list(resource.getrlimit(resource.RLIMIT_NOFILE))))'
            return subprocess.run([sys.executable, '-B', '-c', code], **kwargs)

    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, original[1]))
        scope, _, _ = runner(resource, PythonChild())
        result = scope['run'](['zig','c++']+[f'object{i}.o' for i in range(300)]+['-o','output.o'])
        observed = json.loads(result['stdout'])
        assert result['rc'] == 0
        assert observed == [needed, original[1]]
        assert resource.getrlimit(resource.RLIMIT_NOFILE) == (needed, original[1])
        (tmp_path/'benign-child-limit-observation.json').write_text(json.dumps({
            'original':list(original),'parent_after_run':list(resource.getrlimit(resource.RLIMIT_NOFILE)),
            'actual_child':observed,'run':result,'qualification':'Benign Python routed by authored subprocess adapter; no Zig/compiler/link/product launched'},indent=2)+'\n')
    finally:
        resource.setrlimit(resource.RLIMIT_NOFILE, original)
    assert resource.getrlimit(resource.RLIMIT_NOFILE) == original
