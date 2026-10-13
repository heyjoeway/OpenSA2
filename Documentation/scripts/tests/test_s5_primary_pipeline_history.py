"""Actual primary writer output survives pending-to-committed source-pin custody.

All rendering inputs are the existing tiny CPU specimen. Children are limited
here to private Git operations and the exact real receipt-pin checker. No
production repository or active receipt is written; no player/compiler runs.
"""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from test_s5_host_compare import CH, GATE, HS, ROOT, specimen

CHECKER = ROOT / 'Documentation/scripts/check_receipt_pins.py'


def test_actual_primary_receipt_keeps_recording_as_evidence_across_commit(specimen, monkeypatch):
    root, build, run, output = specimen
    actual_run = subprocess.run
    actual_popen = subprocess.Popen
    def allowed(command, cwd):
        args = [str(value) for value in command]
        if args[:3] == ['git', '-C', str(root)]:
            assert cwd is None or Path(cwd) == root
        else:
            assert args == [sys.executable, str(CHECKER), '--root', str(root)]
            assert Path(cwd) == root
        return args
    def checked_run(command, *args, **kwargs):
        allowed(command, kwargs.get('cwd'))
        return actual_run(command, *args, **kwargs)
    def checked_popen(command, *args, **kwargs):
        allowed(command, kwargs.get('cwd'))
        return actual_popen(command, *args, **kwargs)
    def blocked(*args, **kwargs):
        raise AssertionError('history proof attempted an unrelated child/socket')
    monkeypatch.setattr(subprocess, 'run', checked_run)
    monkeypatch.setattr(subprocess, 'Popen', checked_popen)
    monkeypatch.setattr(os, 'system', blocked)
    monkeypatch.setattr(os, 'fork', blocked)
    monkeypatch.setattr(socket, 'socket', blocked)
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_AUTHOR_NAME='authored history owner', GIT_AUTHOR_EMAIL='owner@example.invalid',
               GIT_COMMITTER_NAME='authored history owner', GIT_COMMITTER_EMAIL='owner@example.invalid',
               GIT_AUTHOR_DATE='2026-10-08T00:00:00+00:00', GIT_COMMITTER_DATE='2026-10-08T00:00:00+00:00')
    transcript = []
    def child(name, command):
        done = subprocess.run(command, cwd=root, env=env, capture_output=True, check=False)
        (root / (name + '.stdout')).write_bytes(done.stdout)
        (root / (name + '.stderr')).write_bytes(done.stderr)
        transcript.append({'name': name, 'command': command, 'exit': done.returncode,
                           'stdout_sha256': hashlib.sha256(done.stdout).hexdigest(),
                           'stderr_sha256': hashlib.sha256(done.stderr).hexdigest()})
        (root / 'history-commands.json').write_text(json.dumps(transcript, indent=2) + '\n')
        return done
    def git(name, *args):
        done = child(name, ['git', '-C', str(root), *args])
        assert done.returncode == 0, done.stderr
        return done
    def pins(name):
        return child(name, [sys.executable, str(CHECKER), '--root', str(root)])

    git('git-init', 'init', '-q', '-b', 'main')
    (root / '.gitignore').write_text('/private\n')
    assert HS.main(['write', '--run', str(run), '--out', str(output)]) == 0
    document = json.loads(output.read_bytes())
    assert HS.RECORDING not in document['inputs']['pipeline']
    first_recording = document['recording']
    assert first_recording == {'path': HS.RECORDING, 'sha256': HS.RECORDING_SHA256}
    git('git-ignore', 'check-ignore', '--', HS.RECORDING)
    git('git-stage-sources', 'add', '--', '.gitignore', 'AGENTS.md', *sorted(document['inputs']['pipeline']))
    git('git-commit-sources', 'commit', '-q', '-m', 'authored producing source inputs')
    assert child('git-recording-not-tracked', ['git', '-C', str(root), 'ls-files', '--error-unmatch', '--', HS.RECORDING]).returncode == 1
    pending = pins('pins-pending-good')
    assert pending.returncode == 0, pending.stderr
    assert b'checked against not committed yet' in pending.stdout
    original = output.read_bytes()
    git('git-stage-good', 'add', '--', GATE.COMPARE_RECEIPT)
    git('git-commit-good', 'commit', '-q', '-m', 'authored actual primary receipt')
    settled = pins('pins-settled-good')
    assert settled.returncode == 0, settled.stderr
    assert b'checked against last committed' in settled.stdout
    assert output.read_bytes() == original
    assert json.loads(output.read_bytes())['recording'] == first_recording

    bad = json.loads(original)
    bad['inputs']['pipeline'][HS.RECORDING] = first_recording['sha256']
    bad_relative = 'Documentation/receipts/authored-misclassified-private-v1.json'
    (root / bad_relative).write_text(json.dumps(bad, indent=1) + '\n')
    bad_pending = pins('pins-pending-bad')
    assert bad_pending.returncode == 0, bad_pending.stderr
    git('git-stage-bad', 'add', '--', bad_relative)
    git('git-commit-bad', 'commit', '-q', '-m', 'authored retained classification failure')
    bad_settled = pins('pins-settled-bad')
    assert bad_settled.returncode == 1
    assert (f'inputs.pipeline pin {HS.RECORDING} names a file that is not present in commit'.encode()
            in bad_settled.stderr)
    assert output.read_bytes() == original
    assert (root / HS.RECORDING).read_bytes()
