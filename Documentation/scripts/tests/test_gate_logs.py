"""Gate-log retention with only tiny authored CPU children, never project gates."""
from pathlib import Path
import json
import sys
import types

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))


@pytest.fixture
def runner():
    path = SCRIPTS / 'run_all_gates.py'
    module = types.ModuleType('_gate_log_test_runner')
    module.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), 'exec'), module.__dict__)
    return module


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    (root / '.git').mkdir()
    (root / 'fixtures').mkdir()
    return root


def child(repo, name, code):
    (repo / 'fixtures' / name).write_text(code)
    return ('fixtures/' + name,)


def invoke(runner, repo, monkeypatch, capsys, gates, *args):
    monkeypatch.setattr(runner, 'GATES', gates)
    monkeypatch.setattr(sys, 'argv', ['run_all_gates.py', '--root', str(repo), *args])
    try:
        runner.main()
    except SystemExit as error:
        failure = str(error.code)
    else:
        failure = None
    output = capsys.readouterr().out
    return failure, output


def records(repo):
    runs = list((repo / 'private/generated/gate-runs').iterdir())
    return runs, [(path, json.loads(path.read_text()))
                  for run in runs for path in sorted(run.glob('*.json'))]


@pytest.mark.parametrize('exit_code, status, failure', [
    (0, 'OK', None),
    (7, 'FAIL', "ALL_GATES_FAILED count=1 gates=['fixture']"),
])
def test_complete_raw_streams_and_exact_exit(runner, repo, monkeypatch, capsys,
                                           exit_code, status, failure):
    stdout = b'first stdout\n' + b'A' * 4096 + b'\nwarning: retained stdout\nstdout tail\xff\n'
    stderr = b'first stderr\n' + b'B' * 4096 + b'\nnote: retained stderr\nstderr tail\xfe\n'
    command = child(repo, 'bytes.py',
                    f'import sys\nsys.stdout.buffer.write({stdout!r})\n'
                    f'sys.stderr.buffer.write({stderr!r})\nsys.exit({exit_code})\n')
    actual_failure, output = invoke(runner, repo, monkeypatch, capsys, (('fixture', command),))
    assert actual_failure == failure
    assert f'{status:7s} fixture' in output
    assert ('stdout tail\ufffd' if exit_code == 0 else 'stderr tail\ufffd') in output
    assert 'warning: retained stdout' in output and 'note: retained stderr' in output
    if exit_code == 0:
        assert 'ALL_GATES_OK count=1' in output
    else:
        assert 'ALL_GATES_OK' not in output
    runs, saved = records(repo)
    assert len(runs) == 1 and str(runs[0]) in output
    assert len(saved) == 1
    _, record = saved[0]
    assert record['gate'] == 'fixture'
    assert record['command'] == [sys.executable, str(repo / 'fixtures/bytes.py')]
    assert record['cwd'] == str(repo)
    assert record['exit'] == exit_code
    assert Path(record['stdout']).read_bytes() == stdout
    assert Path(record['stderr']).read_bytes() == stderr


def test_default_all_and_only_keep_selection_and_counts(runner, repo, monkeypatch, capsys):
    first = child(repo, 'first.py', 'print("first fixture")\n')
    second = child(repo, 'second.py', 'print("second fixture")\n')
    gates = (('first', first), ('second', second))
    failure, output = invoke(runner, repo, monkeypatch, capsys, gates)
    assert failure is None and 'ALL_GATES_OK count=2' in output
    assert 'first fixture' in output and 'second fixture' in output
    failure, output = invoke(runner, repo, monkeypatch, capsys, gates, '--only', 'second')
    assert failure is None and 'ALL_GATES_OK count=1' in output
    assert 'first fixture' not in output and 'second fixture' in output
    runs, saved = records(repo)
    assert len(runs) == 2
    assert sorted(record['gate'] for _, record in saved) == ['first', 'second', 'second']


@pytest.mark.parametrize('command, detail', [
    (('fixtures/absent.py',), 'no such script:'),
    (('-m', 'pytest', 'absent-suite'), 'no such directory:'),
])
def test_missing_suite_still_fails(runner, repo, monkeypatch, capsys, command, detail):
    failure, output = invoke(runner, repo, monkeypatch, capsys, (('missing', command),))
    assert failure == "ALL_GATES_FAILED count=1 gates=['missing']"
    assert 'MISSING missing' in output and detail in output
    assert 'ALL_GATES_OK' not in output
    _, saved = records(repo)
    assert saved == []


def test_failed_child_does_not_stop_later_gate(runner, repo, monkeypatch, capsys):
    bad = child(repo, 'bad.py', 'import sys\nprint("literal failure", file=sys.stderr)\nsys.exit(9)\n')
    good = child(repo, 'good.py', 'print("later gate ran")\n')
    failure, output = invoke(runner, repo, monkeypatch, capsys, (('bad', bad), ('good', good)))
    assert failure == "ALL_GATES_FAILED count=1 gates=['bad']"
    assert 'later gate ran' in output and 'ALL_GATES_OK' not in output
    _, saved = records(repo)
    assert [(record['gate'], record['exit']) for _, record in saved] == [('bad', 9), ('good', 0)]


def test_unknown_gate_still_refused(runner, repo, monkeypatch, capsys):
    command = child(repo, 'unused.py', 'print("must not run")\n')
    failure, output = invoke(runner, repo, monkeypatch, capsys, (('fixture', command),), '--only', 'unknown')
    assert failure == "unknown gate(s): ['unknown']; known are ['fixture']"
    assert output == '' and not (repo / 'private').exists()


def test_unwritable_log_directory_refuses_child(runner, repo, monkeypatch, capsys):
    command = child(repo, 'unused.py',
                    'from pathlib import Path\nPath("child-started").write_text("started")\n')
    base = repo / 'private/generated/gate-runs'
    base.mkdir(parents=True)
    base.chmod(0o500)
    try:
        failure, output = invoke(runner, repo, monkeypatch, capsys, (('fixture', command),))
    finally:
        base.chmod(0o700)
    assert failure is not None and failure.startswith('could not create gate log directory:')
    assert 'ALL_GATES_OK' not in output and not (repo / 'child-started').exists()


def test_log_write_failure_cannot_pass_successful_child(runner, repo, monkeypatch, capsys):
    command = child(repo, 'success.py',
                    'from pathlib import Path\nPath("child-started").write_text("started")\nprint("success")\n')
    original_open = Path.open

    class BrokenLog:
        def __init__(self, file):
            self.file = file

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.file.__exit__(*args)

        def write(self, data):
            raise OSError('literal log write failure')

    def open_file(path, *args, **kwargs):
        file = original_open(path, *args, **kwargs)
        return BrokenLog(file) if path.suffix == '.stdout' else file

    monkeypatch.setattr(Path, 'open', open_file)
    failure, output = invoke(runner, repo, monkeypatch, capsys, (('fixture', command),))
    assert failure == "ALL_GATES_FAILED count=1 gates=['fixture']"
    assert 'FAIL    fixture' in output and 'literal log write failure' in output
    assert 'ALL_GATES_OK' not in output and (repo / 'child-started').read_text() == 'started'
    _, saved = records(repo)
    assert saved[0][1]['exit'] == 0
    assert saved[0][1]['log_error'] == 'literal log write failure'
