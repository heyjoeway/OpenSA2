"""Tiny extracted-production player diagnostic owners. No SDL/host library executes."""
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess

import pytest

REPO = Path('/workspace/OpenSA2')
LOCAL = Path(__file__).parents[3]
SOURCE_ROOT = Path(os.environ.get('SA2_LATENCY_PLAYER_SOURCE_ROOT', str(LOCAL)))
SDL_ROOT = Path(os.environ.get('SA2_LATENCY_PLAYER_SDL_ROOT', str(REPO / 'private/s5-tools/src/SDL3-3.4.16/include')))

def child(command, cwd, folder, env=None):
    """Run `command` with this process's environment less its SA2_ settings (a shell's SA2_TWEAKS or
    SA2_REPLAY_TICKS would reach the extracted main), plus `env`."""
    folder.mkdir(parents=True, exist_ok=False)
    (folder / 'command.json').write_text(json.dumps({'command': command, 'cwd': str(cwd), 'env': env or {}}, indent=2)+'\n')
    environ = {k: v for k, v in os.environ.items() if not k.startswith('SA2_')}
    environ.update(env or {})
    result = subprocess.run(command, cwd=cwd, capture_output=True, timeout=30, env=environ)
    (folder / 'stdout.log').write_bytes(result.stdout)
    (folder / 'stderr.log').write_bytes(result.stderr)
    (folder / 'exit.json').write_text(json.dumps({'exit_code':result.returncode,
        'stdout_sha256':hashlib.sha256(result.stdout).hexdigest(), 'stderr_sha256':hashlib.sha256(result.stderr).hexdigest()},indent=2)+'\n')
    return result

@pytest.fixture(scope='session')
def binary(tmp_path_factory):
    folder=tmp_path_factory.mktemp('player_cpu')
    source=SOURCE_ROOT / 'host/player/player.cpp'
    template=folder/'driver.cpp.in';template.write_text(DRIVER)
    generated=build(source,template,folder/'extraction')
    command=['/usr/bin/g++','-std=c++17','-O0','-g','-Wall','-Wextra','-Wno-unused-parameter',
        '-I'+str(SDL_ROOT),'-I'+str(SOURCE_ROOT/'host/player'),str(generated),
        str(SOURCE_ROOT/'host/player/input.cpp'),str(SOURCE_ROOT/'host/player/sha256.cpp'),
        str(SOURCE_ROOT/'host/player/tweaks.cpp'),
        '-Wl,--wrap=clock_gettime','-Wl,--wrap=fclose','-Wl,--wrap=fflush','-Wl,--wrap=write','-Wl,--wrap=fopen',
        '-o',str(folder/'driver')]
    result=child(command,folder,folder/'compile')
    assert result.returncode==0, result.stderr.decode()
    return folder/'driver'

@pytest.fixture
def run(binary,tmp_path):
    def invoke(case,extra=(),script=None,expected_exit=0,env=None):
        out=tmp_path/'fixture';out.mkdir()
        if script is not None:(out/'script.tsv').write_text(script)
        result=child([str(binary),case,str(out),*extra],tmp_path,tmp_path/'child',env)
        assert result.returncode==expected_exit, result.stderr.decode()+result.stdout.decode()
        observed=json.loads((out/'observed.json').read_text()) if (out/'observed.json').exists() else None
        return out,observed,result
    return invoke

def rows(path):
    return list(csv.DictReader(io.StringIO(path.read_text()),delimiter='\t'))

def test_dequeue_clock_and_fifo(run):
    out,doc,_=run('dequeue')
    assert rows(out/'keys.tsv')==[
        dict(change='1',scancode='4',dik='30',down='1',synthetic='0',age_ns='1000000',handled_s='100.001000000',seq='0',sent_s='100.002000000',status='sent'),
        dict(change='2',scancode='4',dik='30',down='0',synthetic='0',age_ns='2000000',handled_s='100.003000000',seq='1',sent_s='100.004000000',status='sent')]
    trace=(out/'trace.txt').read_text().splitlines()
    assert trace[:12]==['clock','poll','clock','ticks','clock','fifo_write','poll','clock','ticks','clock','fifo_write','poll']
    assert doc['host_input']['records_written']==2 and doc['host_input']['key_events']==2
    assert doc['latency_diagnostics']['evidence_ready'] is True

def test_alt_enter_toggles_fullscreen_once_and_never_reaches_the_game(run):
    """Alt+Enter (its down, a repeat and its up) is the window's borderless fullscreen, toggled
    once, and none of its three events reaches the game's keyboard; the next key does."""
    out,doc,_=run('alt-enter')
    got=rows(out/'keys.tsv')
    assert not [r for r in got if r['scancode']=='40']
    assert (got[0]['scancode'],got[0]['down'],got[0]['status'])==('4','1','sent')
    assert doc['host_input']['records_written']==1


def test_a_repeat_with_alt_added_to_the_games_held_enter_stays_the_games(run):
    """Enter held for the game, then Alt pressed: the repeats carry Alt but stay the game's, no
    fullscreen toggle, and the up reaches the game, so its Start is not left down."""
    out,doc,_=run('enter-then-alt')
    enter=[(r['down'],r['status']) for r in rows(out/'keys.tsv') if r['scancode']=='40']
    assert enter[0]==('1','sent') and enter[-1]==('0','sent')


def test_grouped_write_uses_same_prewrite_time(run):
    out,doc,_=run('grouped');got=rows(out/'keys.tsv')
    assert [dict(change=r['change'],dik=r['dik'],seq=r['seq'],sent_s=r['sent_s'],status=r['status']) for r in got[:2]]==[
        dict(change='1',dik='30',seq='0',sent_s='100.006000000',status='sent'),
        dict(change='2',dik='48',seq='0',sent_s='100.006000000',status='sent')]
    assert doc['host_input']['retries']==3

@pytest.mark.parametrize('case,field,error',[('future','future_key_timestamps','future SDL key timestamp'),
    ('clock-error','event_clock_errors','event CLOCK_MONOTONIC sample failed'),
    ('age-error','invalid_key_timestamps','SDL key age exceeds handled clock')])
def test_bad_event_timing_refuses_evidence_preserves_transport(run,case,field,error):
    out,doc,_=run(case);d=doc['latency_diagnostics']
    assert d[field]==1 and d['evidence_ready'] is False and d['error']==error
    assert doc['host_input']['records_written']>=1 and doc['host_input']['changes']>=1
    if case=='future':assert rows(out/'keys.tsv')[0]['age_ns']=='0'

@pytest.mark.parametrize('case,attempted,started,header',[('no-start',False,False,False),('bad-start',True,False,False),('header-fail',True,True,False)])
def test_opened_logs_do_not_invent_start_or_header(run,case,attempted,started,header):
    out,doc,_=run(case);d=doc['latency_diagnostics']
    assert (d['sender_start_attempted'],d['sender_started'],d['key_log_header_accepted'])==(attempted,started,header)
    assert d['evidence_ready'] is False and d['closed'] is True
    assert doc['host_input']['records_written']==0

def test_close_pending_rows_before_borrowed_stream_close(run):
    out,doc,_=run('lifetime')
    assert [r['status'] for r in rows(out/'keys.tsv')]==['lost','lost']
    assert doc['host_input']['held_now']==0 and doc['host_input']['key_log']['lost']==2
    assert doc['latency_diagnostics']['key_log_closed'] is True

@pytest.mark.parametrize('case,field',[('close-fail','key_log_owner_io_errors'),('flush-fail',None)])
def test_actual_final_io_failure_is_retained(run,case,field):
    out,doc,_=run(case);d=doc['latency_diagnostics']
    assert d['evidence_ready'] is False and d['error'] is not None
    if field: assert d[field]==1 and d['key_log_closed'] is False
    else: assert doc['present_log']['io_errors']==1
    assert 'io_failure' in (out/'trace.txt').read_text()

def test_audio_final_pump_then_sender_cache_destruction_then_files(run):
    out,doc,_=run('finish-audio');trace=(out/'trace.txt').read_text().splitlines()
    assert trace.index('audio_final_pump')<trace.index('key_file_close')<trace.index('failure_assembly')
    assert trace.count('key_file_close')==1 and trace.count('present_file_close')==1
    assert doc['host_input']['key_events']==1 and doc['host_input']['held_now']==0
    assert rows(out/'keys.tsv')[-1]['down']=='0'

@pytest.mark.parametrize('case,rows_count,errors,failures',[('present',1,0,0),('present-fail',1,0,1),('present-clock',0,1,0),('present-order',0,1,0),('frame-overflow',0,1,0)])
def test_present_samples_surround_host_return(run,case,rows_count,errors,failures):
    out,doc,_=run(case)
    assert doc['present_log']['rows']==rows_count
    assert doc['latency_diagnostics']['present_clock_errors']==errors
    got=rows(out/'presents.tsv');assert len(got)==rows_count
    if rows_count:
        assert got==[dict(frame='1',virtual_us='16666',call_s='200.000000001',returned_s='200.000000123')]
        trace=(out/'trace.txt').read_text().splitlines()
        assert trace[trace.index('present')-1:trace.index('present')+3]==['clock','present','clock','pace_presented']
        # The window's picture is placed (Player::place_picture, authored here) before the present's clock.
        assert trace[trace.index('present')-2:trace.index('present')]==['place_picture','clock']
    trace=(out/'trace.txt').read_text().splitlines()
    assert trace.count('host_failure')==failures
    if case=='frame-overflow':assert 'present' not in trace

@pytest.mark.parametrize('case,pushed,failed,armed',[('synthetic',2,0,True),('synthetic-push-fail',0,2,True),('synthetic-overflow',0,0,False)])
def test_process_queue_synthetic_ns_and_provenance(run,case,pushed,failed,armed):
    out,doc,_=run(case);d=doc['synthetic_keys']
    assert (d['pushed'],d['push_failures'],d['armed'])==(pushed,failed,armed)
    assert d['diagnostic'] is True and d['lines']==2
    assert d['script_sha256']==hashlib.sha256(b'0 4 down\n2 4 up\n').hexdigest()
    if case=='synthetic':
        data=(out/'events.tsv').read_text().splitlines();assert data==[
            '768\t1000000000\t7\t1396781643\t4\t1\t0','769\t1002000000\t7\t1396781643\t4\t0\t0']

@pytest.mark.parametrize('script,loaded',[('0 4 down\n2 4 up\n',True),('01 4 down\n',False),('-1 4 down\n',False),
    ('18446744073710 4 down\n',False),('2 4 down\n1 4 up\n',False),('0 999 down\n',False),('0 0 down\n',False),('0 4 held\n',False),('0 4 down extra\n',False),('\n',False),
    ('0 4 down\n9 0 close\n',True),('0 4 close\n',False),('0 00 close\n',False),('0 0 close extra\n',False)])
def test_synthetic_script_boundary(run,script,loaded):
    out,_,_=run('script',script=script);assert (out/'loaded.txt').read_text()==('1' if loaded else '0')

def kv(path):
    return dict(line.split('\t',1) for line in path.read_text().splitlines())

def test_the_first_close_asks_the_caller_and_a_late_one_stops(run):
    out,doc,_=run('end-request');got=kv(out/'end.tsv')
    assert {k:got[k] for k in ('requested','written','stop','end_fd','error')}=={
        'requested':'1','written':'1','stop':'0','end_fd':'-1','error':''}
    assert got['after_same']=='0\t1'                       # the same click again: counted
    assert got['after_late']=='1\tthe window was closed'   # a close kForceCloseAfterS later
    assert doc['host_input']['open'] is False and doc['host_input']['held_now']==0
    assert [(r['down'],r['status']) for r in rows(out/'keys.tsv')]==[('1','sent'),('0','sent')]

def test_the_caller_s_signal_is_an_end_request_too(run):
    out,doc,_=run('end-request',extra=['signal']);got=kv(out/'end.tsv')
    assert got=={'requested':'1','stop':'0','by':"the caller's request"}
    assert doc['host_input']['open'] is False and doc['host_input']['held_now']==0

def test_a_close_nobody_hears_stops_the_run_as_before(run):
    out,_,_=run('end-request-fail');got=kv(out/'end.tsv')
    assert (got['requested'],got['written'],got['stop'],got['reason'])==('1','0','1','the window was closed')
    assert got['error'].startswith('write: ')

def test_a_scripted_close_is_a_close_request(run):
    out,doc,_=run('synthetic-close');got=kv(out/'synthetic.tsv')
    assert (got['requested'],got['closes'])==('1','1')
    assert doc['synthetic_keys']['closes']==1 and doc['synthetic_keys']['real_keys_ignored']==0

def test_a_synthetic_session_sends_only_its_own_keys(run):
    out,doc,_=run('synthetic-ignores-real');got=kv(out/'synthetic.tsv')
    assert (got['requested'],got['ignored'])==('0','1')
    assert doc['synthetic_keys']['real_keys_ignored']==1
    assert doc['host_input']['key_events']==0 and doc['host_input']['synthetic_key_events']==1

def test_alias_guards_preserve_existing_input_bytes(run):
    out,_,_=run('alias');assert (out/'keys.tsv').read_text()=='sentinel'

@pytest.mark.parametrize('case',['publication','factory-fail','device-fail','implicit-fail','periodic-fail','final-fail','run-first-fail','run-new-fail','run-live-fail'])
def test_real_publication_failure_stops_guarded_calls_and_verdict(run,case):
    out,doc,result=run(case);d=doc['latency_diagnostics']
    assert d['report_io_errors']>=1 and d['evidence_ready'] is False
    trace=(out/'trace.txt').read_text().splitlines()
    assert 'io_failure' in trace and 'reader_open' not in trace and 'create_factory' not in trace and 'create_device' not in trace
    if case in ['final-fail','run-first-fail','run-new-fail','run-live-fail']:
        assert b'verdict: FAIL' in result.stdout and b'verdict: PASS' not in result.stdout

def test_allowed_requested_log_setup_failure_does_not_start_sender(run):
    out,doc,_=run('run-no-start');d=doc['latency_diagnostics']
    assert d['sender_start_attempted'] is False and d['sender_started'] is False and d['key_log_header_accepted'] is False
    assert d['key_log_opened'] is True and d['evidence_ready'] is False

def test_no_options_keep_diagnostic_report_keys_absent(run):
    out,doc,_=run('default');assert doc=={'checks':doc['checks']}

BASE_ARGS=['--interactive','--video-fd','3','--cancel-fd','4','--input-path','keys.fifo','--key-log','keys.tsv','--present-log','presents.tsv']
@pytest.mark.parametrize('extra,expected',[(['--max-frames','1'],2),(['--synthetic-keys','script.tsv'],2),
    (['--synthetic-keys','script.tsv','--max-frames','0'],2),(['--synthetic-keys','script.tsv','--max-frames','01'],2),
    (['--synthetic-keys','script.tsv','--max-frames','-1'],2),(['--synthetic-keys','script.tsv','--max-frames','4294967296'],2),
    (['--synthetic-keys','script.tsv','--max-frames','1x'],2),(['--synthetic-keys','script.tsv','--max-frames','1'],0),
    (['--synthetic-keys','script.tsv','--max-frames','4294967295'],0),
    (['--synthetic-keys','script.tsv','--max-frames','1','--hidden'],2),
    (['--synthetic-keys','script.tsv','--max-frames','1','--stats-only'],2),
    (['--synthetic-keys','script.tsv','--max-frames','1','--allow-truncated'],2),
    (['--end-fd','5'],0),(['--end-fd','3'],2),(['--end-fd','4'],2),(['--end-fd','2'],2)])
def test_live_bound_exception_only_checked_synthetic_mode(run,extra,expected):
    run('parser',extra=BASE_ARGS+extra,expected_exit=expected)

@pytest.mark.parametrize('args',[['x.sa2s','--input-path','keys.fifo'],['x.sa2s','--interactive'],
    ['--interactive','--video-fd','3','--cancel-fd','3'],['--interactive','--video-fd','3','--cancel-fd','4','--hidden'],
    ['--interactive','--video-fd','3','--cancel-fd','4','--stats-only'],['x.sa2s','--end-fd','5']])
def test_existing_interactive_input_guards_remain(run,args):
    run('parser',extra=args,expected_exit=2)


# main's host-tweak lines (host/player/tweaks.h) run as extracted, with the real tweaks.cpp: a play
# session reads SA2_TWEAKS, and a synthetic run, a session outside play and a replay ignore it, by name.
PLAY_ARGS=BASE_ARGS+['--end-fd','5']
@pytest.mark.parametrize('args,env,line',[
    (PLAY_ARGS,{},None),
    (PLAY_ARGS,{'SA2_TWEAKS':''},None),
    (PLAY_ARGS,{'SA2_TWEAKS':'no-event-bars'},'sa2 host tweaks (player): none on (SA2_TWEAKS=no-event-bars)'),
    (PLAY_ARGS,{'SA2_TWEAKS':'no-event-bars','SA2_REPLAY_TICKS':'x'},
        'sa2 host tweaks (player): SA2_TWEAKS=no-event-bars ignored in a replay (SA2_REPLAY_TICKS or SA2_REPLAY_WORDS is set)'),
    (PLAY_ARGS,{'SA2_TWEAKS':'no-event-bars','SA2_REPLAY_WORDS':''},
        'sa2 host tweaks (player): SA2_TWEAKS=no-event-bars ignored in a replay (SA2_REPLAY_TICKS or SA2_REPLAY_WORDS is set)'),
    (BASE_ARGS+['--synthetic-keys','script.tsv','--max-frames','1'],{'SA2_TWEAKS':'no-event-bars'},
        'sa2 host tweaks (player): SA2_TWEAKS=no-event-bars ignored in a synthetic-input run (--synthetic-keys or --synthetic-input)'),
    (['--interactive','--video-fd','3','--cancel-fd','4','--end-fd','5'],{'SA2_TWEAKS':'no-event-bars'},
        'sa2 host tweaks (player): SA2_TWEAKS=no-event-bars ignored outside play (play is --interactive with --input-path)')])
def test_main_reads_host_tweaks_in_play_alone(run,args,env,line):
    _,_,result=run('parser',extra=args,env=env)
    lines=[l for l in result.stderr.decode().splitlines() if l.startswith('sa2 host tweaks')]
    assert lines==([line] if line else [])

def test_ready_requires_each_actual_start_and_header_field(run):
    out,doc,_=run('readiness-fields')
    # Counterfactual flags isolate the pure readiness predicate after a real started/header/closure path.
    assert (out/'readiness.tsv').read_text().splitlines()==['all\t1','started\t0','key_header\t0','present_header\t0']
    assert doc['latency_diagnostics']['evidence_ready'] is True

def test_synthetic_queue_dequeue_to_real_sender_fifo(run):
    out,doc,_=run('synthetic-delivery')
    assert doc['host_input']['synthetic_key_events']==2 and doc['host_input']['key_events']==0
    assert doc['host_input']['records_written']==2 and doc['synthetic_keys']['pushed']==2
    assert [(r['down'],r['synthetic'],r['seq'],r['age_ns']) for r in rows(out/'keys.tsv')]==[('1','1','0','0'),('0','1','1','0')]
    assert doc['latency_diagnostics']['requested_synthetic'] is True

@pytest.mark.parametrize('case',['report-close-fail','report-rename-fail'])
def test_real_publisher_close_and_rename_failures_preserve_prior_destination(run,case):
    out,doc,_=run(case);assert doc['latency_diagnostics']['report_io_errors']==1
    assert doc['latency_diagnostics']['evidence_ready'] is False
    assert 'io_failure' in (out/'trace.txt').read_text()


@pytest.mark.parametrize('kind',['direct-key','direct-present','hard-key','hard-present','symlink-key','symlink-present'])
def test_expectation_alias_refused_before_any_output_write(run,kind):
    out,doc,_=run('expect-alias',extra=[kind])
    assert (out/'expected.tsv').read_text()=='frame\tx\ty\tlo\thi\tb\tg\tr\ttol\twhat\n1\t0\t0\t0\t3\t0\t0\t0\t0\texpectation input sentinel\n'
    assert not (out/'report.json').exists()
    assert doc['latency_diagnostics']['key_log_opened'] is False and doc['present_log']['opened'] is False

"""Extract exact candidate production spans for the bounded CPU callback driver."""
import hashlib
import json
from pathlib import Path

def build(source, template, out):
    source = Path(source)
    text = source.read_text()
    bindings = []
    def take(label, start, end, include_end=False):
        a = text.index(start)
        b = text.index(end, a + len(start)) + (len(end) if include_end else 0)
        span = text[a:b]
        bindings.append({'label': label, 'first_line': text[:a].count('\n')+1,
                         'last_line': text[:b].count('\n')+1,
                         'bytes': len(span.encode()), 'sha256': hashlib.sha256(span.encode()).hexdigest(),
                         'start': start, 'end': end, 'include_end': include_end})
        return span
    def function(label, signature):
        a = text.index(signature)
        brace = text.index('{', a)
        i, depth, state = brace, 0, 'code'
        while i < len(text):
            c, n = text[i], text[i:i+2]
            if state == 'line':
                if c == '\n': state = 'code'
            elif state == 'block':
                if n == '*/': state = 'code'; i += 1
            elif state in ('"', "'"):
                if c == '\\': i += 1
                elif c == state: state = 'code'
            elif n == '//': state = 'line'; i += 1
            elif n == '/*': state = 'block'; i += 1
            elif c in ('"', "'"): state = c
            elif c == '{': depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    span=text[a:i+1]
                    bindings.append({'label':label, 'first_line':text[:a].count('\n')+1,
                                     'last_line':text[:i+1].count('\n')+1, 'bytes':len(span.encode()),
                                     'sha256':hashlib.sha256(span.encode()).hexdigest(), 'signature':signature})
                    return span
            i+=1
        raise AssertionError('unterminated '+signature)
    free = '\n'.join(function(x,x) for x in ['double now_s()', 'std::string jesc(', 'std::string jstr(', 'std::string strf(',
            'std::string sdl_event_name(', 'const char* event_kind(', 'bool sdl_event_is_input(', 'std::string refuse_output('])
    free += '\n'+take('latency helpers','// Input latency clock and publication helpers.', '// End input latency helpers.', True)
    free += '\n'+take('Options','struct Options {','struct Expect {')
    free += '\n'+take('force close and end signal','constexpr double kForceCloseAfterS',
                     'void on_end_signal(int) { g_end_signal = 1; }\n',True)
    fields=take('latency fields','    std::unique_ptr<sa2_input::KeySender> input_', '\n    // accounting')
    methods='\n'.join(function(x,'void Player::'+x+'(') for x in ['diagnostic_failure','finish_input_diagnostics','push_due_synthetic_keys','pump_events','request_end','do_present'])
    methods+='\n'+'\n'.join(function(x,ret+' Player::'+x+'(') for x,ret in [
        ('report_failure','bool'),('diagnostic_path_refusal','std::string'),('open_input_diagnostics','bool'),
        ('diagnostic_evidence_ready','bool'),('write_present_row','bool'),('load_synthetic_keys','bool'),
        ('diagnostic_json','std::string'),('implicit_surfaces','bool')])
    starter=take('actual start/header branch','            if (input_) {\n                sender_start_attempted_ = true;','\n        }\n        phase_ = "live_open";')
    starter='int Player::starter() {\n'+starter+'\n    return 0;\n}'
    run=take('run prefix','int Player::run() {','    // A live stream\'s schedule moves')+'    return 0;\n}'
    # Prefix uses the production guard/host/start/reader code exactly. Later replay is outside this proof.
    run=run.replace('int Player::run() {','int Player::run_prefix() {',1)
    report_guard=take('report entry guard','bool Player::write_report(bool complete) {','    auto env =')
    report_tail=take('report publisher','    std::string error;\n    if (!publish_report_file(o_.report, s, &error))','\n}\n',True)
    report_payload=take('input report payload','    if (input_) s += input_->report_json();','    s += diagnostic_json();',True)
    report=report_guard+'\n    std::string s = "{\\n";\n'+report_payload+'\n    s += "  \\"fixture\\": true\\n}\\n";\n'+report_tail
    factory=take('factory required report guard','    phase_ = "Direct3DCreate9";','    D3DADAPTER_IDENTIFIER9 id = {};')
    device=take('device required report guard','    phase_ = "CreateDevice";','    phase_ = "replay";',True)
    final=take('final publication and verdict','    if (win_) {\n        const SDL_WindowFlags f = SDL_GetWindowFlags(win_);','\n}\n\n// DXVK\'s log', True)
    final='int Player::final_guard() {\n    const char* scope="";\n'+final[:-len('\n\n// DXVK\'s log')]
    finish_order='    finish_audio();\n    finish_input_diagnostics();'
    assert finish_order in text
    bindings.append({'label':'finish input ordering','literal':finish_order,'first_line':text[:text.index(finish_order)].count('\n')+1,
                     'sha256':hashlib.sha256(finish_order.encode()).hexdigest(),'bytes':len(finish_order)})
    parser='\n'.join(function(x,x) for x in ['bool parse_draw_checkpoint(', 'bool parse_list('])
    main=function('entire CLI main','int main(int argc, char** argv)')
    parser+='\n'+main.replace('int main(int argc, char** argv)','int parser_main(int argc, char** argv)',1)
    idle=take('post report failed replay guard','        if (o_.explicit_live() && !report_io_failed_) on_idle();','    if (present_pending_ && !stop_) do_present(nullptr);')
    # Keep the entire real post-record branch inside a one-record authored loop.
    replacements={'FREE':free,'FIELDS':fields,'METHODS':methods,'RUN_PREFIX':run,'STARTER':starter,'REPORT':report,
                  'FACTORY':factory,'DEVICE':device,'FINAL':final,'PARSER':parser,'FINISH_ORDER':finish_order,'IDLE':idle}
    result=Path(template).read_text()
    for key,value in replacements.items():
        marker='@@'+key+'@@';assert result.count(marker)==1,(key,result.count(marker));result=result.replace(marker,value)
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    (out/'driver.cpp').write_text(result)
    (out/'EXTRACTION.json').write_text(json.dumps({'source':str(source),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
        'spans':bindings,'mechanical_transforms':['run() prefix renamed run_prefix(), ends before pace/mixer and returns 0',
            'main() renamed parser_main()', 'Player::place_picture authored (a trace entry): the window is a stand-in', 'report entry and publisher bracket authored JSON payload; full report serializer not compiled',
            'guard and finish spans inserted in authored callback methods',
            "the host tweaks' calls in the extracted spans reach authored no-ops (each tweak's own test runs it)"], 'driver_sha256':hashlib.sha256(result.encode()).hexdigest()},indent=2)+'\n')
    return out/'driver.cpp'

DRIVER = r'''
// Authored CPU callback context. Only @@ spans are extracted production code.
#include <SDL3/SDL.h>
#include <algorithm>
#include <cerrno>
#include <cinttypes>
#include <climits>
#include <csignal>
#include <cmath>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <map>
#include <memory>
#include <set>
#include <string>
#include <vector>
#include <deque>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#include <time.h>
#include "input.h"
#include "prof.h"
#include "sha256.h"
#include "tweaks.h"
#include <functional>
// The host tweaks' calls in the extracted spans reach these authored stand-ins, which do nothing:
// each tweak's own test runs its code.
namespace sa2tw {
void hold_while_unfocused(SDL_Window*,SDL_AudioStream*,bool,const std::function<const char*()>&,const std::function<void()>&){}  // pause-unfocused
namespace tex_filter { void start(){} }  // tex-filter
}
namespace fs=std::filesystem;
#define SA2_PLAYER_GPU 0
using HRESULT=int32_t;
#define SUCCEEDED(hr) ((hr)>=0)
#define FAILED(hr) ((hr)<0)
constexpr int kExitPass=0,kExitUsage=2;
constexpr int T_DEVICE=1,kAudioFrameBytes=8;
constexpr unsigned D3D_SDK_VERSION=32,D3DADAPTER_DEFAULT=0,D3DDEVTYPE_HAL=1;
static std::vector<std::string> trace;
static std::deque<int64_t> clock_samples;
static uint64_t ticks_ns=1000000000;
static std::deque<SDL_Event> events;
static bool push_ok=true, parser_only=false, fifo_eagain=false, report_close_failure=false;
static FILE* watched_keys=nullptr;
static FILE* watched_presents=nullptr;
static FILE* fail_flush=nullptr;
static FILE* fail_close=nullptr;
static int lifetime_violations=0;
static int publication_number=0;
static int fail_publication_number=0;
static int checks=0;
extern "C" FILE* __real_fopen(const char*,const char*);
extern "C" FILE* __wrap_fopen(const char* path,const char* mode) {
    const std::string p=path;
    if(p.size()>=15 && p.substr(p.size()-15)=="report.json.tmp" && mode[0]=='w'){
        ++publication_number;
        if(publication_number==fail_publication_number){errno=EACCES;return nullptr;}
    }
    FILE* f=__real_fopen(path,mode);
    if(f && report_close_failure && p.size()>=15 && p.substr(p.size()-15)=="report.json.tmp")fail_close=f;
    return f;
}
static bool lifetime_check();
#define CHECK(x) do { ++checks; if(!(x)) { std::fprintf(stderr,"check failed line %d: %s\n",__LINE__,#x);std::exit(21); } } while(0)
extern "C" int __real_clock_gettime(clockid_t,struct timespec*);
extern "C" int __wrap_clock_gettime(clockid_t id, struct timespec* t) {
    CHECK(id==CLOCK_MONOTONIC);trace.push_back("clock");
    int64_t n=300000000000LL;if(!clock_samples.empty()){n=clock_samples.front();clock_samples.pop_front();}
    if(n<0){errno=EIO;return -1;}t->tv_sec=n/1000000000;t->tv_nsec=n%1000000000;return 0;
}
extern "C" int __real_fclose(FILE*);
extern "C" int __wrap_fclose(FILE* f) {
    if(f==watched_keys){trace.push_back("key_file_close");if(!lifetime_check()){++lifetime_violations;std::fprintf(stderr,"borrowed key FILE closed before sender destruction/cache\n");std::exit(21);}watched_keys=nullptr;}
    if(f==watched_presents){trace.push_back("present_file_close");watched_presents=nullptr;}
    const bool fail=f==fail_close;int result=__real_fclose(f);if(fail){fail_close=nullptr;errno=EIO;return EOF;}return result;
}
extern "C" int __real_fflush(FILE*);
extern "C" int __wrap_fflush(FILE* f) {
    if(f==fail_flush){fail_flush=nullptr;errno=EIO;return EOF;}return __real_fflush(f);
}
extern "C" ssize_t __real_write(int,const void*,size_t);
extern "C" ssize_t __wrap_write(int fd,const void* data,size_t n) {
    if(n==sa2_input::kRecordBytes){trace.push_back("fifo_write");if(fifo_eagain){errno=EAGAIN;return -1;}}
    return __real_write(fd,data,n);
}
extern "C" bool SDL_PollEvent(SDL_Event* e) {
    trace.push_back("poll");if(events.empty())return false;*e=events.front();events.pop_front();return true;
}
extern "C" Uint64 SDL_GetTicksNS(void) {trace.push_back("ticks");return ticks_ns;}
extern "C" SDL_WindowID SDL_GetWindowID(SDL_Window*) {return 7;}
extern "C" SDL_WindowFlags SDL_GetWindowFlags(SDL_Window*) {return SDL_WINDOW_INPUT_FOCUS;}
extern "C" bool SDL_SetWindowFullscreen(SDL_Window*, bool) {trace.push_back("fullscreen");return true;}
extern "C" const char* SDL_GetError(void) {return "";}
extern "C" bool SDL_PushEvent(SDL_Event* e) {trace.push_back("push");if(!push_ok)return false;events.push_back(*e);return true;}
extern "C" int SDL_GetAudioStreamQueued(SDL_AudioStream*) {return 0;}
@@FREE@@
struct Sa2Record { uint64_t virtual_us=16666;uint32_t seq=1; };
struct MStat { uint64_t failed=0;uint32_t first_fail_hr=0,first_fail_seq=0; };
struct Sa2Pace { struct Slot{double target=0,due=0,moved=0;};Slot schedule(uint64_t,double){trace.push_back("pace_schedule");return{};}void presented(uint64_t,const Slot&,double){trace.push_back("pace_presented");} };
struct Sa2Mixer {static constexpr int kRate=48000;void on_frame(uint64_t){} };
struct FakeDevice {HRESULT result=0;HRESULT Present(void*,void*,void*,void*){trace.push_back("present");return result;} };
struct FakeFactory {HRESULT CreateDevice(unsigned,unsigned,void*,unsigned,void*,FakeDevice** dev){trace.push_back("create_device");static FakeDevice d;*dev=&d;return 0;} };
struct Surf {enum Kind{BACKBUFFER,AUTODEPTH};Kind kind;void*host=nullptr;bool made=false;uint32_t w=0,h=0,fmt=0;};
struct Pin{std::string path,real,sha256;};
struct FakeReader {void set_live_hashing(bool){}int open_live(int,int,void*,void*,int){trace.push_back("reader_open");return 0;}enum{OPEN_OK=0,OPEN_IO=1};int open(const std::string&,std::string*){trace.push_back("reader_open");return 0;} };
using Sa2StreamReader=FakeReader;
static int setup_failure=0;
struct Player {
    explicit Player(const Options& o):o_(o),video_fd_(o.video_fd),cancel_fd_(o.cancel_fd){}
    ~Player(){finish_input_diagnostics();}
    Options o_;
@@FIELDS@@
    bool sdl_up_=true,stop_=false,report_ok_=true,opened_=false,stream_file_=false;
    bool records_to_end_=true,end_records_=true,stream_problems_=false,finalized_=false;
    bool present_pending_=false,any_frame_time_=false,any_frame_us_=false,stats_device_=false;
    uint32_t frame_=0,present_seq_=1,bb_w_=640,bb_h_=480,bb_fmt_=22,rt_addr_=10,ds_addr_=11;
    uint32_t guest_pp_[13]={},host_behavior_=0;
    uint16_t cw_before_=0,cw_after_=0;
    uint64_t last_vus_=0,missing_frame_records_=0,first_frame_us_=0,draws_frame_=0,source_draws_frame_=0;
    uint64_t events_discarded_=0,input_events_=0,close_requests_=0;
    uint64_t frames_occluded_=0,frames_hidden_=0,frames_minimized_=0,frames_input_focus_=0,frames_mouse_focus_=0,present_failures_=0;
    double last_pump_=0,longest_unpumped_=0,t_start_=0;
    SDL_Window*win_=reinterpret_cast<SDL_Window*>(1);SDL_AudioStream*audio_stream_=nullptr;
    FILE*pace_log_=nullptr;
    FakeDevice device_;FakeDevice*dev_=&device_;FakeFactory factory_;FakeFactory*d3d_=&factory_;
    void*bb_=nullptr;void*ds_=nullptr;
    std::unique_ptr<Sa2Pace>pace_;std::unique_ptr<Sa2Mixer>mixer_;
    std::map<uint32_t,MStat>mstat_;std::map<std::string,uint64_t>events_by_kind_;
    struct FlagRun{uint32_t first,last;uint64_t flags;};std::vector<FlagRun>flag_runs_;
    std::vector<std::string>window_log_,basic_failures_,failures_;
    std::map<std::string,Pin>files_;std::map<uint32_t,Surf>surf_;
    std::string phase_,stop_reason_,window_end_,live_status_,live_detail_;
    int exit_status_=0,video_fd_=3,cancel_fd_=4;
    FakeReader rd_;
    static void*service_thunk;
    bool host()const{return !o_.stats_only;}
    void io_failure(const std::string&s){basic_failures_.push_back("i/o: "+s);trace.push_back("io_failure");}
    void usage_failure(const std::string&s){basic_failures_.push_back("usage: "+s);}
    void stream_problem(const std::string&){stream_problems_=true;}
    void note_error(const std::string&){trace.push_back("host_failure");}
    bool device_failure(const std::string&){return false;}
    void readback_frame(uint64_t,const Sa2Record*){trace.push_back("readback");}
    void place_picture(){trace.push_back("place_picture");}
    void audio_block(){}
    const char* hold_ending()const{return nullptr;}  // pause-unfocused's ending(): the session plays
    void service_pads(){}
    void pace_until(double){trace.push_back("pace_wait");}
    void read_dxvk_log(){trace.push_back("dxvk_read");}
    std::string dxvk_log_path()const{return {};}
    bool check_outputs(){trace.push_back("output_guard");return true;}
    bool open_frames_out(){return true;}
    bool open_pace_log(){return true;}
    void pin_host_files(){trace.push_back("host_pin");}
    bool validate_host_files(){trace.push_back("host_validate");return setup_failure!=1;}
    bool setup_sdl(){trace.push_back("sdl_setup_callback");return setup_failure!=2;}
    bool setup_window(uint32_t,uint32_t){trace.push_back("window_setup_callback");input_window_id_=7;return setup_failure!=3;}
    bool accept_live_outcome(int){return true;}
    void on_idle(){trace.push_back("idle_dispatch");}
    void finish_audio(){trace.push_back("audio_final_pump");pump_events();}
    void teardown(){finish_input_diagnostics();trace.push_back("teardown");}
    int finish(){finish_input_diagnostics();failures_=basic_failures_;exit_status_=failures_.empty()?0:2;return final_guard();}
    int run(){if(parser_only){trace.push_back("parser_accepted");return 0;}return run_prefix();}
    bool write_report(bool);
    bool report_failure(const std::string&);
    void diagnostic_failure(const std::string&,bool fatal=true);
    std::string diagnostic_path_refusal()const;
    bool open_input_diagnostics();
    void finish_input_diagnostics();
    bool diagnostic_evidence_ready()const;
    bool write_present_row(uint32_t,uint64_t,double,double);
    bool load_synthetic_keys();
    void push_due_synthetic_keys();
    std::string diagnostic_json()const;
    void pump_events();void request_end(double now,bool by_close);void do_present(const Sa2Record*);
    bool implicit_surfaces();int run_prefix();int final_guard();int starter();
    bool factory_guard();bool device_guard();void idle_guard();int finish_order();
};
void*Player::service_thunk=nullptr;
static Player* watched_player=nullptr;
static bool lifetime_check(){return !watched_player || (!watched_player->input_ && !watched_player->final_host_input_.empty());}
@@METHODS@@
@@RUN_PREFIX@@
@@STARTER@@
@@REPORT@@
bool Player::factory_guard(){
    auto create9=[](unsigned){trace.push_back("create_factory");static FakeFactory f;return &f;};
@@FACTORY@@
    return true;
}
uint16_t x87_cw(){return 0x37f;}
bool Player::device_guard(){
    int pp=0;using HWND=void*;
@@DEVICE@@
    return true;
}
@@FINAL@@
void Player::idle_guard(){for(int once=0;once<1;++once){
@@IDLE@@
}
int Player::finish_order(){
@@FINISH_ORDER@@
    trace.push_back("failure_assembly");failures_=basic_failures_;exit_status_=failures_.empty()?0:2;return final_guard();
}
@@PARSER@@
// Authored cases use literal outputs and real KeySender/FIFO calls.
static std::string slurp(const std::string& path){std::ifstream f(path);return std::string(std::istreambuf_iterator<char>(f),{});}
static SDL_Event key(SDL_Scancode scan,bool down,uint64_t stamp=999000000,uint32_t window=7,bool synthetic=false){
    SDL_Event e{};e.type=down?SDL_EVENT_KEY_DOWN:SDL_EVENT_KEY_UP;e.key.windowID=window;e.key.scancode=scan;e.key.down=down;e.key.timestamp=stamp;e.key.which=synthetic?sa2_input::kSyntheticKeyboard:1;return e;
}
static Options options(const std::string& dir){Options o;o.input_path=dir+"/keys.fifo";o.key_log=dir+"/keys.tsv";o.present_log=dir+"/presents.tsv";o.report=dir+"/report.json";o.report_every=0;return o;}
static int reader(const std::string& path){CHECK(mkfifo(path.c_str(),0600)==0);int fd=open(path.c_str(),O_RDONLY|O_NONBLOCK|O_CLOEXEC);CHECK(fd>=0);return fd;}
static void start(Player& p){p.input_=std::make_unique<sa2_input::KeySender>(p.o_.input_path);CHECK(p.open_input_diagnostics());p.sender_start_attempted_=true;p.sender_started_=p.input_->start();CHECK(p.sender_started_);p.key_log_header_accepted_=p.key_log_file_&&p.input_->log_ok();p.input_window_id_=7;watched_player=&p;watched_keys=p.key_log_file_;watched_presents=p.present_log_file_;}
static std::string report(Player&p){std::string s="{\n";s+=p.final_host_input_.empty()?(p.input_?p.input_->report_json():""):p.final_host_input_;s+=p.diagnostic_json();s+="  \"checks\": "+std::to_string(checks)+"\n}\n";return s;}
static void done(Player&p,const std::string&dir){p.finish_input_diagnostics();CHECK(lifetime_violations==0);std::ofstream(dir+"/observed.json")<<report(p);std::ofstream t(dir+"/trace.txt");for(auto&s:trace)t<<s<<'\n';}
int main(int argc,char**argv){
    CHECK(argc>=3);std::string mode=argv[1],dir=argv[2];fs::create_directories(dir);
    if(mode=="parser"){parser_only=true;return parser_main(argc-2,argv+2);}
    Options o=options(dir);Player p(o);
    if(mode=="dequeue"){
        int fd=reader(o.input_path);start(p);trace.clear();clock_samples={100000000000LL,100001000000LL,100002000000LL,100003000000LL,100004000000LL};
        events={key(SDL_SCANCODE_A,true),key(SDL_SCANCODE_A,false,998000000)};p.pump_events();
        uint8_t bytes[528];CHECK(read(fd,bytes,sizeof bytes)==528);CHECK(bytes[8+0x1e]==0x80&&bytes[264+8+0x1e]==0);close(fd);
        done(p,dir);return 0;
    }
    if(mode=="alt-enter"){
        int fd=reader(o.input_path);start(p);trace.clear();
        SDL_Event d=key(SDL_SCANCODE_RETURN,true),r=key(SDL_SCANCODE_RETURN,true),u=key(SDL_SCANCODE_RETURN,false,998000000);
        d.key.key=r.key.key=u.key.key=SDLK_RETURN;d.key.mod=r.key.mod=SDL_KMOD_LALT;r.key.repeat=true;
        events={d,r,u,key(SDL_SCANCODE_A,true,997000000)};p.pump_events();
        uint8_t bytes[528];CHECK(read(fd,bytes,sizeof bytes)==264);CHECK(bytes[8+0x1c]==0&&bytes[8+0x1e]==0x80);close(fd);
        CHECK(std::count(trace.begin(),trace.end(),std::string("fullscreen"))==1);
        done(p,dir);return 0;
    }
    if(mode=="enter-then-alt"){
        int fd=reader(o.input_path);start(p);trace.clear();
        SDL_Event d=key(SDL_SCANCODE_RETURN,true),r=key(SDL_SCANCODE_RETURN,true),u=key(SDL_SCANCODE_RETURN,false,998000000);
        d.key.key=r.key.key=u.key.key=SDLK_RETURN;r.key.mod=SDL_KMOD_LALT;r.key.repeat=true;
        events={d,r,u};p.pump_events();
        uint8_t bytes[528];CHECK(read(fd,bytes,sizeof bytes)==528);CHECK(bytes[8+0x1c]==0x80&&bytes[264+8+0x1c]==0);close(fd);
        CHECK(std::count(trace.begin(),trace.end(),std::string("fullscreen"))==0);
        done(p,dir);return 0;
    }
    if(mode=="grouped"){
        int fd=reader(o.input_path);start(p);fifo_eagain=true;clock_samples={100000000000LL,100001000000LL,100002000000LL,100003000000LL,100004000000LL,100005000000LL};events={key(SDL_SCANCODE_A,true),key(SDL_SCANCODE_B,true)};p.pump_events();fifo_eagain=false;clock_samples={100006000000LL};p.input_->pump();
        uint8_t bytes[264];CHECK(read(fd,bytes,sizeof bytes)==264);CHECK(bytes[8+0x1e]==0x80&&bytes[8+0x30]==0x80);done(p,dir);close(fd);return 0;
    }
    if(mode=="future"||mode=="clock-error"||mode=="age-error"){
        int fd=reader(o.input_path);start(p);if(mode=="clock-error")clock_samples={100000000000LL,-1,100003000000LL};
        if(mode=="age-error")ticks_ns=UINT64_MAX;
        events={key(SDL_SCANCODE_A,true,mode=="future"?1000000001:1)};p.pump_events();done(p,dir);close(fd);return 0;
    }
    if(mode=="no-start"||mode=="bad-start"||mode=="header-fail"){
        p.input_=std::make_unique<sa2_input::KeySender>(o.input_path);CHECK(p.open_input_diagnostics());watched_player=&p;watched_keys=p.key_log_file_;watched_presents=p.present_log_file_;
        if(mode!="no-start"){
            if(mode=="header-fail"){CHECK(mkfifo(o.input_path.c_str(),0600)==0);fail_flush=p.key_log_file_;}
            p.starter();
        }
        done(p,dir);return 0;
    }
    if(mode=="lifetime"||mode=="close-fail"||mode=="flush-fail"){
        CHECK(mkfifo(o.input_path.c_str(),0600)==0);start(p);events={key(SDL_SCANCODE_A,true)};p.pump_events();
        if(mode=="close-fail")fail_close=p.key_log_file_;if(mode=="flush-fail")fail_flush=p.present_log_file_;
        done(p,dir);return 0;
    }
    if(mode=="finish-audio"){
        int fd=reader(o.input_path);start(p);events={key(SDL_SCANCODE_A,true)};CHECK(p.finish_order()==0);CHECK(lifetime_violations==0);CHECK(!p.input_&&!p.key_log_file_&&!p.present_log_file_);p.finish_input_diagnostics();done(p,dir);close(fd);return 0;
    }
    if(mode=="present"||mode=="present-fail"||mode=="present-clock"||mode=="present-order"||mode=="frame-overflow"){
        CHECK(p.open_input_diagnostics());p.sdl_up_=false;p.pace_=std::make_unique<Sa2Pace>();trace.clear();
        clock_samples={199000000000LL,200000000001LL,200000000123LL};if(mode=="present-clock")clock_samples={199000000000LL,-1,200000000100LL,200000000200LL};
        if(mode=="present-order")clock_samples={199000000000LL,200000000123LL,200000000001LL};if(mode=="present-fail")p.device_.result=-1;
        if(mode=="frame-overflow")p.frame_=UINT32_MAX;Sa2Record r;p.do_present(&r);done(p,dir);return 0;
    }
    if(mode=="synthetic"||mode=="synthetic-push-fail"||mode=="synthetic-overflow"||mode=="script"){
        p.o_.synthetic_keys=dir+"/script.tsv";
        if(mode!="script"){std::ofstream(p.o_.synthetic_keys)<<"0 4 down\n2 4 up\n";}
        CHECK(p.open_input_diagnostics());bool loaded=p.load_synthetic_keys();
        if(mode=="script"){std::ofstream(dir+"/loaded.txt")<<(loaded?1:0);done(p,dir);return 0;}CHECK(loaded);
        p.sdl_up_=false;p.input_window_id_=7;clock_samples={200000000001LL,200000000123LL};ticks_ns=mode=="synthetic-overflow"?UINT64_MAX:1000000000;Sa2Record r;p.do_present(&r);
        push_ok=mode!="synthetic-push-fail";p.push_due_synthetic_keys();
        if(mode!="synthetic-overflow"){ticks_ns=1002000000;p.push_due_synthetic_keys();}
        std::ofstream ef(dir+"/events.tsv");for(const auto&e:events)ef<<e.type<<'\t'<<e.key.timestamp<<'\t'<<e.key.windowID<<'\t'<<e.key.which<<'\t'<<e.key.scancode<<'\t'<<e.key.down<<'\t'<<e.key.repeat<<'\n';
        done(p,dir);return 0;
    }
    if(mode=="readiness-fields"){
        CHECK(mkfifo(o.input_path.c_str(),0600)==0);start(p);p.finish_input_diagnostics();CHECK(p.diagnostic_evidence_ready());
        std::ofstream f(dir+"/readiness.tsv");f<<"all\t1\n";
        p.sender_started_=false;f<<"started\t"<<p.diagnostic_evidence_ready()<<'\n';p.sender_started_=true;
        p.key_log_header_accepted_=false;f<<"key_header\t"<<p.diagnostic_evidence_ready()<<'\n';p.key_log_header_accepted_=true;
        p.present_log_header_accepted_=false;f<<"present_header\t"<<p.diagnostic_evidence_ready()<<'\n';p.present_log_header_accepted_=true;
        done(p,dir);return 0;
    }
    if(mode=="synthetic-delivery"){
        int fd=reader(o.input_path);p.o_.synthetic_keys=dir+"/script.tsv";std::ofstream(p.o_.synthetic_keys)<<"0 4 down\n2 4 up\n";start(p);CHECK(p.load_synthetic_keys());
        p.sdl_up_=false;clock_samples={200000000001LL,200000000123LL};ticks_ns=1000000000;Sa2Record r;p.do_present(&r);p.sdl_up_=true;
        p.pump_events();ticks_ns=1002000000;p.pump_events();uint8_t bytes[528];CHECK(read(fd,bytes,sizeof bytes)==528);
        CHECK(bytes[8+0x1e]==0x80&&bytes[264+8+0x1e]==0);done(p,dir);close(fd);return 0;
    }
    if(mode=="report-close-fail"||mode=="report-rename-fail"){
        if(mode=="report-close-fail"){std::ofstream(o.report)<<"{\"old\":true}\n";report_close_failure=true;}
        else CHECK(fs::create_directory(o.report));
        CHECK(!p.write_report(false));CHECK(p.stop_&&p.report_io_errors_==1);
        if(mode=="report-close-fail")CHECK(slurp(o.report)=="{\"old\":true}\n");else CHECK(fs::is_directory(o.report));
        CHECK(slurp(o.report+".tmp").find("fixture")!=std::string::npos);report_close_failure=false;done(p,dir);return 0;
    }
    if(mode=="expect-alias"){
        CHECK(argc==4);const std::string kind=argv[3];const std::string expected=dir+"/expected.tsv";
        const std::string original="frame\tx\ty\tlo\thi\tb\tg\tr\ttol\twhat\n1\t0\t0\t0\t3\t0\t0\t0\t0\texpectation input sentinel\n";
        std::ofstream(expected)<<original;p.o_.expect=expected;
        std::string& log=kind.find("key")!=std::string::npos?p.o_.key_log:p.o_.present_log;
        if(kind.find("direct")!=std::string::npos)log=expected;
        else if(kind.find("hard")!=std::string::npos)CHECK(link(expected.c_str(),log.c_str())==0);
        else CHECK(symlink(expected.c_str(),log.c_str())==0);
        int got=p.run_prefix();CHECK(got==2);CHECK(slurp(expected)==original);CHECK(!fs::exists(p.o_.report));
        CHECK(std::find(trace.begin(),trace.end(),"output_guard")==trace.end());done(p,dir);return 0;
    }
    if(mode=="alias"){
        p.o_.report=dir+"/keys.tsv";CHECK(!p.diagnostic_path_refusal().empty());p.o_.report=dir+"/report.json";
        std::ofstream(o.key_log)<<"sentinel";CHECK(link(o.key_log.c_str(),o.present_log.c_str())==0);CHECK(!p.diagnostic_path_refusal().empty());
        CHECK(slurp(o.key_log)=="sentinel");std::ofstream(dir+"/observed.json")<<"{\"checks\": "<<checks<<"}\n";return 0;
    }
    if(mode=="publication"){
        CHECK(p.write_report(false));CHECK(slurp(o.report).find("fixture")!=std::string::npos);
        fs::create_directory(o.report+".tmp"); // fails if normal tmp unexpectedly remains
        CHECK(!p.write_report(false));CHECK(p.stop_&&p.report_io_failed_&&p.report_io_errors_==1);CHECK(!p.basic_failures_.empty());
        done(p,dir);return 0;
    }
    if(mode=="factory-fail"||mode=="device-fail"||mode=="implicit-fail"||mode=="periodic-fail"||mode=="final-fail"){
        fs::create_directory(o.report+".tmp");
        if(mode=="factory-fail")CHECK(!p.factory_guard());if(mode=="device-fail")CHECK(!p.device_guard());if(mode=="implicit-fail")CHECK(!p.implicit_surfaces());
        if(mode=="periodic-fail"){p.sdl_up_=false;p.o_.report_every=1;p.o_.max_frames=1;clock_samples={200000000001LL,200000000123LL};Sa2Record r;p.do_present(&r);CHECK(p.stop_reason_=="the required report could not be written");}
        if(mode=="final-fail"){CHECK(p.final_guard()==2);CHECK(!p.failures_.empty());}
        CHECK(p.stop_&&p.report_io_failed_);p.o_.video_fd=3;p.idle_guard();CHECK(std::find(trace.begin(),trace.end(),"idle_dispatch")==trace.end());
        CHECK(std::find(trace.begin(),trace.end(),"create_factory")==trace.end());CHECK(std::find(trace.begin(),trace.end(),"create_device")==trace.end());done(p,dir);return 0;
    }
    if(mode=="run-no-start"||mode=="run-first-fail"||mode=="run-new-fail"||mode=="run-live-fail"){
        p.o_.video_fd=3;p.o_.cancel_fd=4;p.o_.stream="-";p.o_.interactive=true;
        CHECK(mkfifo(o.input_path.c_str(),0600)==0);if(mode=="run-no-start")setup_failure=3;
        if(mode=="run-first-fail")fs::create_directory(o.report+".tmp");
        if(mode=="run-new-fail")fail_publication_number=2;if(mode=="run-live-fail")fail_publication_number=3;
        int got=p.run_prefix();if(mode!="run-no-start")CHECK(got==2&&p.stop_);
        CHECK(std::find(trace.begin(),trace.end(),"reader_open")==trace.end());done(p,dir);return 0;
    }
    if(mode=="end-request"||mode=="end-request-fail"){
        // The first close asks the caller (one byte on --end-fd), lets the key up and closes
        // the sender; one within kForceCloseAfterS is counted; a later one stops the run.
        int fd=reader(o.input_path);int end[2];CHECK(pipe(end)==0);
        if(mode=="end-request-fail")close(end[0]);
        if(argc>3&&std::string(argv[3])=="signal"){                     // the caller's SIGUSR1
            p.o_.interactive=true;p.o_.end_fd=end[1];p.end_fd_=end[1];start(p);
            events={key(SDL_SCANCODE_A,true)};p.pump_events();on_end_signal(SIGUSR1);p.pump_events();
            char b=0;CHECK(read(end[0],&b,1)==1&&b==1);
            std::ofstream(dir+"/end.tsv")<<"requested\t"<<p.end_requested_<<"\nstop\t"<<p.stop_<<"\nby\t"<<p.end_by_<<'\n';
            uint8_t bytes[528];CHECK(read(fd,bytes,sizeof bytes)==528);CHECK(bytes[8+0x1e]==0x80&&bytes[264+8+0x1e]==0);
            done(p,dir);close(fd);return 0;
        }
        p.o_.interactive=true;p.o_.end_fd=end[1];p.end_fd_=end[1];start(p);
        SDL_Event c{};c.type=SDL_EVENT_WINDOW_CLOSE_REQUESTED;c.window.windowID=7;
        events={key(SDL_SCANCODE_A,true),c};p.pump_events();
        std::ofstream f(dir+"/end.tsv");
        f<<"requested\t"<<p.end_requested_<<"\nwritten\t"<<p.end_written_<<"\nstop\t"<<p.stop_<<"\nreason\t"<<p.stop_reason_
         <<"\nend_fd\t"<<p.end_fd_<<"\nerror\t"<<p.end_error_<<'\n';
        if(mode=="end-request"){
            char b=0;CHECK(read(end[0],&b,1)==1&&b==1);CHECK(read(end[0],&b,1)==0);   // one byte, then the write end closed
            uint8_t bytes[528];CHECK(read(fd,bytes,sizeof bytes)==528);CHECK(bytes[8+0x1e]==0x80&&bytes[264+8+0x1e]==0);
            events={c};p.pump_events();f<<"after_same\t"<<p.stop_<<'\t'<<p.closes_after_end_<<'\n';
            clock_samples={304000000000LL};events={c};p.pump_events();
            f<<"after_late\t"<<p.stop_<<'\t'<<p.stop_reason_<<'\n';
        }
        f.close();done(p,dir);close(fd);return 0;
    }
    if(mode=="synthetic-close"||mode=="synthetic-ignores-real"){
        int fd=reader(o.input_path);p.o_.synthetic_keys=dir+"/script.tsv";
        std::ofstream(p.o_.synthetic_keys)<<(mode=="synthetic-close"?"0 0 close\n":"0 4 down\n");
        int end[2];CHECK(pipe(end)==0);p.o_.interactive=true;p.o_.end_fd=end[1];p.end_fd_=end[1];
        start(p);CHECK(p.load_synthetic_keys());
        p.sdl_up_=false;clock_samples={200000000001LL,200000000123LL};ticks_ns=1000000000;Sa2Record r;p.do_present(&r);p.sdl_up_=true;
        if(mode=="synthetic-ignores-real")events={key(SDL_SCANCODE_B,true)};
        p.pump_events();
        std::ofstream f(dir+"/synthetic.tsv");
        f<<"requested\t"<<p.end_requested_<<"\nignored\t"<<p.real_keys_ignored_<<"\ncloses\t"<<p.synthetic_closes_<<'\n';
        if(mode=="synthetic-close"){char b=0;CHECK(read(end[0],&b,1)==1&&b==1);}
        else{uint8_t bytes[264];CHECK(read(fd,bytes,sizeof bytes)==264);CHECK(bytes[8+0x1e]==0x80&&bytes[8+0x30]==0);}
        f.close();done(p,dir);close(fd);return 0;
    }
    if(mode=="default"){
        p.o_=Options{};CHECK(p.diagnostic_json().empty());CHECK(p.write_report(false));p.do_present(nullptr);done(p,dir);return 0;
    }
    CHECK(false);return 22;
}

'''
