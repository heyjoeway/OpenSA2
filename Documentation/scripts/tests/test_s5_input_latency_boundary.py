"""Authored closed producer bytes. No process, renderer, compiler or device."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

import pytest

PATH = (Path(__file__).parents[1] / 'check_s5_exit.py').resolve()
SPEC = importlib.util.spec_from_file_location('s5_latency_boundary_private', PATH)
X = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = X
SPEC.loader.exec_module(X)
assert Path(X.__file__).resolve() == PATH
assert sys.modules[SPEC.name] is X
assert X.HeldLatencyFile.__module__ == SPEC.name

KEYS = ('change\tscancode\tdik\tdown\tsynthetic\tage_ns\thandled_s\tseq\tsent_s\tstatus\n'
        '3\t40\t28\t1\t0\t0\t1.800000000\t-\t-\tlost\n'
        '1\t40\t28\t1\t0\t100000000\t1.200000500\t0\t1.300000500\tsent\n'
        '2\t40\t28\t0\t0\t100000000\t1.200000500\t0\t1.300000500\tsent\n'
        '4\t40\t28\t1\t0\t0\t1.900000000\t-\t-\tunsent\n'
        '5\t40\t28\t1\t0\t0\t3.200000000\t1\t3.300000000\tsent\n')
POLLS = ('poll\tfirst_seq\tlast_seq\trecords\ttick\tpresents\tmono_ns\n'
         '5\t0\t0\t1\t2\t1\t1400000500\n'
         '8\t1\t1\t1\t3\t3\t3400000000\n'
         '9\t2\t2\t1\t4\t3\t3500000000\n')
PRESENTS = ('frame\tvirtual_us\tcall_s\treturned_s\n'
            '1\t0\t0.800000000\t0.900000000\n'
            '2\t16667\t1.600000500\t1.700000500\n'
            '3\t33334\t2.600000000\t2.700000000\n')
ARRIVALS = ('frame\tarrived_s\n1\t0.700000000\n2\t1.500000500\n3\t2.500000000\n')
D100 = {'min': 100000, 'max': 100000, 'mean': 100000, 'p50': 100000, 'p90': 100000, 'p99': 100000}
D500 = {'min': 500000, 'max': 500000, 'mean': 500000, 'p50': 500000, 'p90': 500000, 'p99': 500000}
D600 = {'min': 600000, 'max': 600000, 'mean': 600000, 'p50': 600000, 'p90': 600000, 'p99': 600000}
COMPONENTS = {'queue': D100, 'send': D100, 'poll_wait': D100,
              'game': D100, 'player': D100, 'present_call': D100}
EXPECTED = {
    'method': 'v1', 'clock': 'CLOCK_MONOTONIC', 'press_at': 'SDL key event timestamp',
    'presented_at': 'host Present returned',
    'presses': [
        {'press': 1, 'dik': 28, 'seq': 0, 'poll': 5, 'tick': 2, 'frame': 2, 'status': 'presented',
         'total_us': 600000, 'from_dequeue_us': 500000, 'queue_us': 100000, 'send_us': 100000,
         'poll_wait_us': 100000, 'game_us': 100000, 'player_us': 100000, 'present_call_us': 100000},
        # Never sent, so placed by the frame on the screen at the dequeue (1.8 and 1.9: frame 2,
        # returned at 1.7000005, which tick 2 made): in the stage.
        {'press': 3, 'dik': 28, 'seq': None, 'poll': None, 'tick': None, 'frame': None,
         'status': 'lost', 'shown_tick': 2},
        {'press': 4, 'dik': 28, 'seq': None, 'poll': None, 'tick': None, 'frame': None,
         'status': 'unsent', 'shown_tick': 2},
        {'press': 5, 'dik': 28, 'seq': 1, 'poll': 8, 'tick': 3, 'frame': 4, 'status': 'unpresented'}],
    'summary': {'presses': 4,
                'by_status': {'presented': 1, 'lost': 1, 'unsent': 1, 'unpresented': 1},
                'total_us': D600, 'from_dequeue_us': D500, 'components_us': COMPONENTS,
                'stage': {'presses': 4,
                          'by_status': {'presented': 1, 'lost': 1, 'unsent': 1, 'unpresented': 1},
                          'total_us': D600, 'from_dequeue_us': D500, 'components_us': COMPONENTS}}}


@pytest.fixture(autouse=True)
def one_press_is_enough_for_the_authored_session(monkeypatch):
    """The authored session presents one press in the stage; the clause's floor of
    MIN_LATENCY_PRESSES is held by its own test below."""
    monkeypatch.setattr(X, 'MIN_LATENCY_PRESSES', 1)


def put(doc, dotted, value):
    parts = dotted.split('.')
    for part in parts[:-1]:
        doc = doc.setdefault(part, {})
    doc[parts[-1]] = value


def hold(root, name, data):
    if type(data) is dict:
        data = (json.dumps(data) + '\n').encode()
    elif type(data) is str:
        data = data.encode()
    path = name + '.tsv' if name not in ('poll_report', 'relay_report') else name + '.json'
    (root / path).write_bytes(data)
    return X.HeldLatencyFile(path, hashlib.sha256(data).hexdigest(), data)


def meta_header(named, change=None):
    """The recorder's header as the player reports it, its meta naming the host `named`
    (play_live's SA2_REC_META); `change` sets (or, with None, drops) meta members."""
    meta = {'run': 'authored-01', 'runner': 'tools/s5/play_live.py', 'interactive': True,
            'scene': 'metalharbor', 'host': named}
    change = change or {}
    for key, value in change.items():
        if value is None:
            del meta[key]
        else:
            meta[key] = value
    return json.dumps({'meta': json.dumps(meta)})


def fixture(root):
    files = {name: hold(root, name, raw) for name, raw in
             [('keys', KEYS), ('polls', POLLS), ('presents', PRESENTS), ('relay_frames', ARRIVALS)]}
    diagnostics = {
        'method': 'v1', 'clock': 'CLOCK_MONOTONIC', 'event_age_clock': 'SDL_GetTicksNS',
        'event_age_unit': 'ns', 'handled_at': 'after successful SDL_PollEvent',
        'presented_at': 'host Present returned', 'requested_keys': True, 'requested_presents': True,
        'requested_synthetic': False, 'sender_start_attempted': True, 'sender_started': True,
        'key_log_header_accepted': True, 'present_log_header_accepted': True,
        'key_log_opened': True, 'key_log_closed': True, 'closed': True, 'evidence_ready': True,
        'error': None, 'key_log_owner_io_errors': 0, 'future_key_timestamps': 0,
        'event_clock_errors': 0, 'invalid_key_timestamps': 0, 'present_clock_errors': 0,
        'report_io_errors': 0}
    report = {
        'latency_diagnostics': diagnostics,
        'host_input': {'started': True, 'open': False, 'held_now': 0, 'owed': False,
                       'key_events': 4, 'changes': 4, 'write_errors': 0, 'last_error': None,
                       'synthetic_key_events': 0, 'records_written': 3, 'last_seq': 2,
                       'key_log': {'path': 'keys.tsv', 'rows': 5, 'sent': 3, 'lost': 1, 'unsent': 1,
                                   'io_errors': 0, 'error': None}},
        'present_log': {'path': 'presents.tsv', 'rows': 3, 'opened': True, 'closed': True,
                        'io_errors': 0, 'dropped_rows': 0, 'error': None},
        'complete': True, 'pass': True, 'exit_status': 0, 'stage': 3,
        'stream_records_read_to_end': True, 'stream_sha256_after_stop': False,
        'records': {'call': 7, 'd3dx': 0, 'frame': 3, 'note': 0, 'end': 1, 'audio': 2,
                    'unknown': 0, 'seq_gaps': 0, 'after_end': 0},
        'presents_without_frame_record': 0,
        'options': {'max_frames': 0, 'allow_truncated': False, 'stats_only': False, 'hidden': False},
        'presents': 3, 'frame_records': 3, 'stream_file_bytes_read': 8000, 'stream_bytes': 8000,
        'stream_header': meta_header('dxvk-native')}
    for key in ('present_failures', 'readback_failures', 'errors', 'calls.not_handled',
                'calls.skipped_by_stage', 'textures.placeholder_binds', 'host_failures.count',
                'stream_problems.count', 'notes.count', 'window.frames_with_flags.hidden',
                'window.frames_with_flags.minimized', 'window.frames_with_flags.occluded'):
        put(report, key, 0)
    run = {'ok': True, 'dinput': {'host_input': {'source': 'fifo', 'script_lines': 0,
            'seq_gaps': 0, 'partial_dropped': 0, 'records': 3, 'last_seq': 2, 'polls': 10,
            'keys_written': 5}}, 'd3d9': {'frames': {'present': 3}},
            'pump': {'status': 0, 'iterations': 4, 'quit_by_host': 1, 'quit_by_guest': 0}}
    live = {'schema': 'sa2-s5-play-live/v1', 'scene': 'metalharbor', 'build': 'authored',
            'profile': 'default', 'commands': {'game': ['authored-game'], 'player': ['authored-player'],
                                              'player_env': ''},
            'runner_sha256': 'a' * 64, 'latency_transport_source': {'path': 'tools/s5/present_live.py',
                                                                 'sha256': 'b' * 64},
            'ticks_observer': {'path': 'observer.c', 'sha256': 'c' * 64},
            'limits': {'stream_cap_bytes': 9000, 'disk_floor_bytes': 1000},
            'run_record': {'ok': True, 'pump': copy.deepcopy(run['pump'])},
            'session': {'game_rc': 0, 'player_rc': 0, 'interrupted': False, 'player_cancelled': False,
                        'player_error': None, 'limit': None, 'stop': None,
                        'relay': {'end': 'the writer closed the pipe', 'framing_problem': None,
                                  'feed_error': None, 'frames_arrived': 3, 'bytes': 8000, 'records': 13}}}
    common = {'method': 'v1', 'clock': 'CLOCK_MONOTONIC', 'unit': 'ns', 'requested': True,
              'opened': True, 'finalized': True, 'closed': True, 'ok': True,
              'clock_errors': 0, 'io_errors': 0, 'error': None}
    poll = common | {'schema': 'sa2-s5-play-polls/v1', 'path': 'polls.tsv', 'dropped_rows': 0,
                     'callbacks': 3, 'rows': 3, 'records_observed': 3, 'records_logged': 3,
                     'first_seq': 0, 'last_seq': 2, 'last_poll': 9}
    relay = common | {'schema': 'sa2-s5-relay-arrivals/v1', 'path': 'relay_frames.tsv',
                       'dropped_frames': 0, 'rows': 3, 'complete_frames': 3, 'framing_problem': None,
                       'feed_error': None, 'feed_ended': True, 'transport_end': 'the writer closed the pipe',
                       'clock_info': {'monotonic': True, 'adjustable': False,
                                      'implementation': 'authored CLOCK_MONOTONIC'},
                       'session_cleanup_errors': None, 'bytes_read': 8000, 'record_headers_seen': 13}
    files.update(poll_report=hold(root, 'poll_report', poll), relay_report=hold(root, 'relay_report', relay))
    return {'root': root, 'what': 'authored boundary', 'host': 'dxvk-native', 'files': files,
            'report': report, 'run': run, 'live_session': live,
            'ticks': [{'tick': 1, 'frames': 1, 'keys': 0}, {'tick': 2, 'frames': 2, 'keys': 4},
                      {'tick': 3, 'frames': 3, 'keys': 1}, {'tick': 4, 'frames': 3, 'keys': 0}],
            'entry': 1, 'goal': 2}


def change_sidecar(bundle, name, key, value):
    doc = json.loads(bundle['files'][name].data)
    put(doc, key, value)
    bundle['files'][name] = hold(bundle['root'], name, doc)


def test_literal_complete_result_and_both_host_component_ids(tmp_path):
    b = fixture(tmp_path)
    for host in ('dxvk-native', 'sdl3-gpu'):
        b['host'] = host
        b['report']['stream_header'] = meta_header(host)
        actual = X.latency_evidence(**b)
        assert X.same_json_value(EXPECTED, actual) is True
        assert actual == EXPECTED


@pytest.mark.parametrize('host, change, said', [
    ('sdl3-gpu', {}, "host is 'dxvk-native', expected 'sdl3-gpu'"),
    ('dxvk-native', {'host': 'sdl3-gpu'}, "host is 'sdl3-gpu', expected 'dxvk-native'"),
    ('dxvk-native', {'host': None}, 'missing host'),
    ('dxvk-native', {'host': ['dxvk-native']}, "host is ['dxvk-native']")])
def test_the_stream_meta_names_the_session_s_host(tmp_path, host, change, said):
    """play_live writes the host into SA2_REC_META: a stream another host's session recorded,
    or one naming none, is not this host's session."""
    b = fixture(tmp_path)
    b['host'] = host
    b['report']['stream_header'] = meta_header('dxvk-native', change)
    with pytest.raises(X.Refused, match=re.escape(said)):
        X.latency_evidence(**b)


def test_held_byte_parser_never_reopens_named_files(tmp_path):
    b = fixture(tmp_path)
    for held in b['files'].values():
        (tmp_path / held.path).write_bytes(b'changed after graph capture\n')
    assert X.latency_evidence(**b) == EXPECTED
    # Current named-path stability belongs to the caller, explicitly outside this component.


@pytest.mark.parametrize('value', [None, True, False, 0.0, '0'])
def test_synthetic_counter_requires_explicit_integer_zero(tmp_path, value):
    b = fixture(tmp_path); b['report']['host_input']['synthetic_key_events'] = value
    with pytest.raises(X.Refused, match='synthetic_key_events'):
        X.latency_evidence(**b)


def test_missing_synthetic_counter(tmp_path):
    b = fixture(tmp_path); del b['report']['host_input']['synthetic_key_events']
    with pytest.raises(X.Refused, match='missing host_input.synthetic_key_events'):
        X.latency_evidence(**b)


@pytest.mark.parametrize('change', [1, 2, 3, 4, 5])
def test_raw_synthetic_press_lost_unsent_release_cannot_be_hidden(tmp_path, change):
    b = fixture(tmp_path); rows = KEYS.splitlines()
    for i in range(1, len(rows)):
        cells = rows[i].split('\t')
        if cells[0] == str(change):
            cells[4] = '1'; rows[i] = '\t'.join(cells)
    b['files']['keys'] = hold(tmp_path, 'keys', '\n'.join(rows) + '\n')
    with pytest.raises(X.Refused, match='synthetic raw key row'):
        X.latency_evidence(**b)


@pytest.mark.parametrize('key,value', [
    ('latency_diagnostics.closed', False), ('latency_diagnostics.evidence_ready', False),
    ('latency_diagnostics.key_log_closed', False), ('latency_diagnostics.sender_started', False),
    ('latency_diagnostics.requested_synthetic', True), ('latency_diagnostics.error', 'IO'),
    ('latency_diagnostics.event_clock_errors', 1), ('latency_diagnostics.future_key_timestamps', 1),
    ('latency_diagnostics.key_log_owner_io_errors', 1), ('latency_diagnostics.report_io_errors', 1),
    ('host_input.open', True), ('host_input.owed', True), ('host_input.held_now', 1),
    ('host_input.last_error', 'write'), ('host_input.key_log.rows', 4), ('host_input.key_log.sent', 2),
    ('host_input.key_log.io_errors', 1), ('host_input.key_log.path', 'wrong'),
    ('present_log.closed', False), ('present_log.rows', 2), ('present_log.dropped_rows', 1),
    ('complete', False), ('pass', False), ('exit_status', 1), ('stage', 3.0),
    ('records.end', 0), ('records.end', 2), ('records.after_end', 1), ('records.unknown', 1),
    ('stream_records_read_to_end', False), ('stream_sha256_after_stop', True),
    ('options.max_frames', 1), ('options.stats_only', True), ('options.allow_truncated', True),
    ('options.hidden', True), ('frame_records', 2), ('stream_bytes', 7999),
    ('calls.not_handled', 1), ('window.frames_with_flags.occluded', 1)])
def test_player_constituents_refuse_forged_ready_and_pass(tmp_path, key, value):
    b = fixture(tmp_path); put(b['report'], key, value)
    with pytest.raises(X.Refused): X.latency_evidence(**b)


@pytest.mark.parametrize('name,key,value', [
    ('poll_report', 'closed', False), ('poll_report', 'ok', False),
    ('poll_report', 'records_logged', 2), ('poll_report', 'callbacks', 2),
    ('poll_report', 'first_seq', 1), ('poll_report', 'last_poll', 8),
    ('poll_report', 'clock_errors', 1), ('poll_report', 'rows', 3.0),
    ('relay_report', 'closed', False), ('relay_report', 'feed_ended', False),
    ('relay_report', 'transport_end', 'limit'), ('relay_report', 'framing_problem', 'partial'),
    ('relay_report', 'feed_error', 'broken'), ('relay_report', 'dropped_frames', 1),
    ('relay_report', 'session_cleanup_errors', [{'operation': 'close', 'error': 'IO'}]),
    ('relay_report', 'session_cleanup_errors', False), ('relay_report', 'clock_info.monotonic', False),
    ('relay_report', 'clock_info.adjustable', True), ('relay_report', 'bytes_read', 7999),
    ('relay_report', 'record_headers_seen', 12)])
def test_sidecar_constituents_and_terminal_coverage(tmp_path, name, key, value):
    b = fixture(tmp_path); change_sidecar(b, name, key, value)
    with pytest.raises(X.Refused): X.latency_evidence(**b)


@pytest.mark.parametrize('value', [True, 3.0, '3', None])
def test_exact_runtime_and_sender_count_types(tmp_path, value):
    b = fixture(tmp_path); b['run']['dinput']['host_input']['records'] = value
    with pytest.raises(X.Refused, match='integer count'): X.latency_evidence(**b)
    b = fixture(tmp_path); b['report']['host_input']['records_written'] = value
    with pytest.raises(X.Refused, match='integer count'): X.latency_evidence(**b)


@pytest.mark.parametrize('key,value', [
    ('session.game_rc', 1), ('session.player_rc', 1), ('session.player_rc', False),
    ('session.interrupted', True), ('session.player_cancelled', True),
    ('session.player_error', 'launch'), ('session.limit', {'what': 'disk'}),
    ('session.stop', {'how': "SIGKILL to loop_run's group"}),
    ('session.stop', {'how': 'a second SIGTERM'}),
    ('session.stop', {'how': "no game process to ask: SIGKILL to loop_run's group"}),
    ('session.stop', {'how': None}), ('session.stop', 'the game had ended'),
    ('session.relay.bytes', 7999), ('session.relay.records', 12),
    ('session.relay.frames_arrived', 2), ('session.relay.feed_error', 'broken'),
    ('run_record.pump.quit_by_host', 0), ('run_record.ok', False),
    ('runner_sha256', 'invalid'), ('commands.player_env', {}), ('limits.disk_floor_bytes', True)])
def test_actual_session_terminal_and_summary_mismatch(tmp_path, key, value):
    b = fixture(tmp_path); put(b['live_session'], key, value)
    with pytest.raises(X.Refused): X.latency_evidence(**b)


@pytest.mark.parametrize('name,raw,message', [
    ('polls', POLLS.rsplit('9\t', 1)[0], 'incomplete FIFO range coverage'),
    ('polls', POLLS.replace('5\t0\t0\t1', '5\t1\t1\t1'), 'tile whole records from 0'),
    ('polls', POLLS.replace('8\t1\t1\t1', '8\t0\t1\t2'), 'incomplete FIFO range coverage'),
    ('polls', POLLS.replace('9\t2\t2\t1', '11\t2\t2\t1'), 'exceeds runtime polls'),
    ('keys', KEYS.replace('2\t40\t28\t0\t0\t100000000\t1.200000500\t0\t1.300000500\tsent\n', ''), 'key_log.rows'),
    ('presents', PRESENTS.rsplit('3\t', 1)[0], 'present_log.rows'),
    ('relay_frames', ARRIVALS.rsplit('3\t', 1)[0], 'rows'),
    ('keys', KEYS.replace('1.300000500', '1.3'), 'nine-decimal'),
    ('keys', KEYS.replace('1.300000500', '1.200000499'), 'backwards'),
    ('keys', KEYS.replace('1.300000500\tsent', '1.300000501\tsent', 1), 'backwards'),
    ('keys', KEYS.replace('change\t', 'wrong\t', 1), 'header'),
    ('keys', KEYS + '1\t2\n', 'width'),
    ('keys', KEYS[:-1], 'incomplete'),
    ('polls', POLLS.replace('1400000500', '01400000500'), 'canonical'),
    ('relay_frames', ARRIVALS.replace('2.500000000', 'nan'), 'nine-decimal')])
def test_real_raw_corruption_not_loader_failure(tmp_path, name, raw, message):
    b = fixture(tmp_path); b['files'][name] = hold(tmp_path, name, raw)
    if name == 'polls':
        rows = [line.split('\t') for line in raw.splitlines()[1:]]
        for key, value in {'callbacks': len(rows), 'rows': len(rows),
                           'records_observed': sum(int(row[3]) for row in rows),
                           'records_logged': sum(int(row[3]) for row in rows),
                           'first_seq': int(rows[0][1]), 'last_seq': int(rows[-1][2]),
                           'last_poll': int(rows[-1][0])}.items():
            change_sidecar(b, 'poll_report', key, value)
    with pytest.raises(X.Refused, match=message): X.latency_evidence(**b)


def test_sidecar_duplicate_keys_bad_hash_and_missing_file(tmp_path):
    b = fixture(tmp_path); b['files']['poll_report'] = hold(tmp_path, 'poll_report', '{"ok":true,"ok":true}\n')
    with pytest.raises(X.Refused, match='invalid JSON'): X.latency_evidence(**b)
    b = fixture(tmp_path); h = b['files']['keys']
    b['files']['keys'] = X.HeldLatencyFile(h.path, 'f' * 64, h.data)
    with pytest.raises(X.Refused, match='sha256'): X.latency_evidence(**b)
    b = fixture(tmp_path); del b['files']['relay_report']
    with pytest.raises(X.Refused, match='missing latency file'): X.latency_evidence(**b)


def test_empty_cleanup_and_cooperative_stop_and_sender_clear_count_are_valid(tmp_path):
    b = fixture(tmp_path); change_sidecar(b, 'relay_report', 'session_cleanup_errors', [])
    b['live_session']['session']['stop'] = {'how': "SIGTERM: WM_QUIT at the game's next keyboard poll"}
    b['report']['host_input']['changes'] = 1
    assert X.latency_evidence(**b) == EXPECTED


@pytest.mark.parametrize('how', ["the game had ended",
                                 "SIGTERM: WM_QUIT at the game's next keyboard poll"])
def test_the_clean_stops_are_the_game_s_own_end_and_the_first_sigterm(tmp_path, how):
    """tools/s5/play_live.py's stop_game: the game found gone, or gone at its next keyboard
    poll after the first SIGTERM. A second SIGTERM meets the runtime's reset handler (the
    default action) and is refused above with SIGKILL."""
    assert X.CLEAN_STOPS == ("the game had ended",
                             "SIGTERM: WM_QUIT at the game's next keyboard poll")
    b = fixture(tmp_path)
    b['live_session']['session']['stop'] = {'how': how, 'pid': 7, 'signalled': [7], 'seconds': 0.2}
    assert X.latency_evidence(**b) == EXPECTED


def test_raw_synthetic_provenance_and_stage_press_requirement(tmp_path):
    b = fixture(tmp_path); b['report']['synthetic_keys'] = {'pushed': 0}
    with pytest.raises(X.Refused, match='synthetic provenance'): X.latency_evidence(**b)
    b = fixture(tmp_path); b.update(entry=2, goal=3)
    with pytest.raises(X.Refused, match='0 presses presented between entering the stage and '
                                        'the goal, under 1'): X.latency_evidence(**b)


def test_the_clause_floor_is_twenty_presented_stage_presses(tmp_path, monkeypatch):
    monkeypatch.undo()
    assert X.MIN_LATENCY_PRESSES == 20
    with pytest.raises(X.Refused, match='1 presses presented between entering the stage and '
                                        'the goal, under 20'):
        X.latency_evidence(**fixture(tmp_path))


@pytest.mark.parametrize('path,value', [
    ('summary.presses', 4.0), ('summary.by_status.presented', True),
    ('summary.total_us.mean', 600000.0), ('summary.total_us.p99', 600000.0),
    ('summary.components_us.send.mean', 100000.0), ('summary.stage.presses', 4.0)])
def test_typed_json_rejects_nested_equal_numeric_substitutions(path, value):
    changed = copy.deepcopy(EXPECTED); put(changed, path, value)
    assert changed == EXPECTED
    assert X.same_json_value(EXPECTED, changed) is False


def test_json_exact_key_sets_list_order_and_nested_press_types():
    changed = copy.deepcopy(EXPECTED); changed['presses'][0]['press'] = True
    assert changed == EXPECTED
    assert X.same_json_value(EXPECTED, changed) is False
    changed = copy.deepcopy(EXPECTED); changed['presses'].reverse()
    assert X.same_json_value(EXPECTED, changed) is False
    changed = copy.deepcopy(EXPECTED); changed['extra'] = 0
    assert X.same_json_value(EXPECTED, changed) is False
    changed = copy.deepcopy(EXPECTED); del changed['summary']['total_us']['p50']
    assert X.same_json_value(EXPECTED, changed) is False
    assert X.same_json_value(EXPECTED, copy.deepcopy(EXPECTED)) is True


def test_callback_index_equal_to_final_poll_count_is_impossible(tmp_path):
    b = fixture(tmp_path); b['run']['dinput']['host_input']['polls'] = 9
    with pytest.raises(X.Refused, match='exceeds runtime polls'):
        X.latency_evidence(**b)
