"""Authored nested CTAB -> real helper -> registered recorder route; CPU only."""
from pathlib import Path
import ast
import hashlib
import json
import os
import struct
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'tools/s5'))
import objcheck as OC
import stream_dump as SD

ZIG = Path('/workspace/OpenSA2/private/predecessor-tools/zig/zig-x86_64-linux-0.15.2/zig')
# Independent literal expectations; never generated from CTAB or captured values.
PAYLOADS = [
    [0x3fa00000,0,0,0], [0xfffffffe,0,1,0], [1],
    [0xc0200000,0,0,0], [0xfffffffe,0,1,0], [1],
    [0x3f800000,0x40000000,0x40400000,0],
    [0x3f800000,0x40000000,0x40400000,0], [0x40a00000,0x40c00000,0x40e00000,0],
    [0x3f800000,0x40a00000,0,0], [0x40000000,0x40c00000,0,0], [0x40400000,0x40e00000,0,0],
]
DEVICE_SLOTS = [94,96,98,109,111,113,94,94,94,109,109,109]
REGISTERS = [7,2,3,7,2,3,11,20,21,24,25,26]
OUTER_SLOTS = [17,15,13,17,15,13,19,21,21]
DELTAS = [1,1,1,1,1,1,1,2,3]
SETTER_TEST = ROOT / 'tools/s5/tests/ctab_recorder_harness.c'
SIX = '(slot == 94u || slot == 96u || slot == 98u || slot == 109u || slot == 111u || slot == 113u)'

def retained(d, label, argv, env):
    assert not (d / (label+'.exit.json')).exists()
    before = {str(Path(p)): hashlib.sha256(Path(p).read_bytes()).hexdigest()
              for p in argv if Path(p).is_file() and Path(p).suffix in ('.c','.h')}
    (d/(label+'.command.json')).write_text(json.dumps({'argv':argv,'cwd':str(d),
        'environment':{k:v for k,v in env.items() if k.startswith(('SA2_', 'ZIG_', 'TMPDIR', 'XDG_'))},
        'source_hashes_before':before},indent=2)+'\n')
    cp = subprocess.run(argv,cwd=d,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    (d/(label+'.stdout')).write_bytes(cp.stdout); (d/(label+'.stderr')).write_bytes(cp.stderr)
    after = {p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in before}
    assert before == after
    (d/(label+'.exit.json')).write_text(json.dumps({'exit':cp.returncode,'source_hashes_after':after,
        'source_changes':[], 'stdout_sha256':hashlib.sha256(cp.stdout).hexdigest(),
        'stderr_sha256':hashlib.sha256(cp.stderr).hexdigest()},indent=2)+'\n')
    return cp

def replace_one(text, before, after):
    assert text.count(before)==1, 'exact mutant source marker'
    return text.replace(before,after,1)

def build(d, control='positive'):
    assert ZIG.is_file(), 'pinned compiler missing'
    env = {k:v for k,v in os.environ.items() if not k.startswith('SA2_')}
    for name in ('tmp','zig-local','zig-global'):(d/name).mkdir()
    env.update(TMPDIR=str(d/'tmp'),ZIG_LOCAL_CACHE_DIR=str(d/'zig-local'),ZIG_GLOBAL_CACHE_DIR=str(d/'zig-global'))
    rec=(ROOT/'host/sa2_present_rec.c').read_text()
    rt=(ROOT/'runtime/sa2_d3d9.c').read_text()
    extra=[]
    if control=='bypass':
        marker='        g_real[idx] = fn;'
        injection='        const uint32_t slot = idx % SA2_D3D9_MAX_SLOTS;\n        if (idx / SA2_D3D9_MAX_SLOTS == 1u && '+SIX+')\n            return __real_sa2_ft_add(va, fn, name, kind);\n'
        rec=replace_one(rec,marker,injection+marker)
    elif control=='global':extra=['-DREC_BREAK_GLOBAL']
    elif control=='restore':
        rt=replace_one(rt,'    g_cur = saved_cur; g_cur_slot = saved_slot;\n    g_cur_seq = saved_seq; g_call_caller = saved_caller;',
                       '    (void)saved_cur; (void)saved_slot; (void)saved_seq; (void)saved_caller;')
    elif control=='blob':
        marker='        case A_IN_VEC:\n        case A_OUT_VEC:\n'
        rec=replace_one(rec,marker,marker+'            if (c->t == 1u && '+SIX.replace('slot','c->slot')+') break;\n')
    elif control=='zero':
        marker='static void rec_post(rec_call *c) {\n'
        rec=replace_one(rec,marker,marker+'    if (c->t == 1u && '+SIX.replace('slot','c->slot')+') {\n        const uint8_t zero[16] = {0};\n        sa2_poke(c->a[1], 16, zero);\n    }\n')
    elif control.startswith('desc-'):
        _, arg, action = control.split('-')
        marker='        case A_OUT:\n'
        body = ('break;' if action=='drop' else
                'blob_guest(c, SA2S_BLOB_OUT, x->a, x->n - 1u); break;')
        rec=replace_one(rec,marker,marker+
                        f'            if (c->t == T_CONSTANTTABLE && c->slot == 6u && x->a == {arg}u) {{ {body} }}\n')
    (d/'recorder.c').write_text(rec); (d/'runtime.c').write_text(rt)
    tree=ast.parse((ROOT/'Documentation/scripts/tests/test_s5_stream.py').read_text())
    getters=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
                 and any(isinstance(t,ast.Name) and t.id=='AUDIO_GETTERS_C' for t in n.targets))
    (d/'audio_getters.c').write_text(getters)
    # A unit build: the device's closed world (runtime/sa2_d3d9.c) would trap the methods off the M7 list this probe drives, so it is compiled open.
    argv=[str(ZIG),'cc','-target','x86_64-linux-musl','-O1','-Wall','-Wextra','-Werror',
          '-DSA2_D3D9_OPEN_WORLD_UNIT_BUILD',
          '-I'+str(ROOT/'runtime'),'-I'+str(ROOT/'host'),str(SETTER_TEST),str(d/'runtime.c'),
          str(ROOT/'runtime/sa2_guest_call.c'),str(d/'recorder.c'),str(d/'audio_getters.c'),
          *['-Wl,-wrap='+s for s in OC.WRAPS],*extra,'-o',str(d/'probe')]
    cp=retained(d,'compile',argv,env)
    assert cp.returncode==0, cp.stderr.decode(errors='replace')
    return env

def execute(d, env, label, recording=True):
    run=d/label;run.mkdir()
    child=dict(env)
    if recording:child.update(SA2_REC_TRACE=str(run/'trace.sa2s'),SA2_REC_COUNTS=str(run/'counts.json'),
                               SA2_REC_META='{"build":"authored-ctab-recorder"}')
    cp=retained(d,label,[str(d/'probe'),str(run)],child)
    rows=[json.loads(s) for s in cp.stdout.splitlines()]
    return cp,rows,run

def audit_bytecode(path,pixel):
    data=path.read_bytes(); ver,token,fourcc=struct.unpack_from('<3I',data)
    assert ver==(0xffff0300 if pixel else 0xfffe0300) and token&0xffff==0xfffe and fourcc==0x42415443
    assert len(data)==12+(token>>16)*4 and data[-4:]==b'\xff\xff\0\0'
    b=data[12:-4];size,creator,version,n,infos,flags,target=struct.unpack_from('<7I',b)
    assert (size,version,n,infos,flags)==(28,ver,6,28,0)
    assert b[creator:creator+18]==b'authored recorder\0'
    assert b[target:target+7]==(b'ps_3_0\0' if pixel else b'vs_3_0\0')
    shapes=[('s',2,7,1,0,3,1,1),('v',2,11,1,1,3,1,3),('i',1,2,1,0,2,1,1),
            ('b',0,3,1,0,1,1,1),('mr',2,20,2,2,3,2,3),('mc',2,24,3,3,3,2,3)]
    for i,(name,rs,reg,count,cl,ty,nr,nc) in enumerate(shapes):
        nm,s,index,c,reserved,t,default=struct.unpack_from('<I4H2I',b,28+20*i)
        assert (s,index,c,reserved,default)==(rs,reg,count,0,0)
        assert b[nm:nm+len(name)+1]==name.encode()+b'\0'
        assert struct.unpack_from('<6HI',b,t)==(cl,ty,nr,nc,1,0,0)

def cpu_observations(rows):
    outer=[r for r in rows if r['event']=='outer']; logs=[r for r in rows if r['event']=='log']
    banks=[r for r in rows if r['event']=='bank']; summary=next(r for r in rows if r['event']=='summary')
    assert len(outer)==9 and len(logs)==21 and len(banks)==12
    assert summary['helper_calls']==12 and summary['max_depth']==1 and summary['failures']==0
    assert [r['words'] for r in banks]==PAYLOADS
    pos=0; at=0; device=summary['device']; scratches=[]
    for o,slot,n in zip(outer,OUTER_SLOTS,DELTAS,strict=True):
        assert o['slot']==slot and o['helper_delta']==n and len(bytes.fromhex(o['state']))==40
        c=logs[pos];pos+=1
        assert (c['type'],c['slot'],c['self'],c['caller'],c['answered'],c['ret'],c['args']) == \
               (15,slot,o['table'],0x401000,2,0,[device,o['handle'],o['input']]), 'outer runtime settlement'
        group=[]
        for k in range(n):
            c=logs[pos];pos+=1
            assert (c['type'],c['slot'],c['self'],c['caller'],c['answered'],c['ret']) == \
                   (1,DEVICE_SLOTS[at],device,0xdead0000,2,0), 'nested runtime route'
            assert c['args'][0]==REGISTERS[at] and c['args'][2]==1
            scratch=c['args'][1];assert 0x4000000<=scratch<0x4400000 and scratch not in (0x300b200,0x300b300)
            group.append(scratch);at+=1
        assert len(set(group))==1
        scratches.append(group[0])
    assert len(set(scratches))==9
    descriptor_observations(rows)
    return outer,logs,summary

def descriptor_observations(rows):
    queries=[r for r in rows if r['event']=='descriptor']
    assert [r['case'] for r in queries]==[0,1,2,3,4]
    for q in queries:
        case=q['case']
        assert (bool(q['desc_arg']),bool(q['count_arg']))==[(True,True),(True,False),(False,True),(False,False),(True,True)][case]
        assert q['result']==(0x8876086c if case==4 else 0)
        assert q['count']==(1 if case in (0,2) else 0xdecafbad)
        if case in (0,1):
            assert 0x4000000<=q['words'][0]<0x4400000
            assert q['words'][1:]==[2,7,1,0,3,1,1,1,0,4,0]
        else:
            assert q['words']==[0x1234abcd]*12
    return queries

def captured_descriptors(trace,calls,rows):
    queries=descriptor_observations(rows)
    recorded=[r for r in calls if r['type']==15 and r['slot']==6]
    assert len(recorded)==5
    for r,q in zip(recorded,queries,strict=True):
        assert int(r['this'],16)==q['table'] and r['thread']==0
        assert [int(x,16) for x in r['args']]==[q['handle'],q['desc_arg'],q['count_arg']]
        assert int(r['eax'],16)==q['result']
        expected=[]
        if q['desc_arg']:
            # The name pointer has an independently checked guest string; all other fields are literal.
            words=([q['words'][0],2,7,1,0,3,1,1,1,0,4,0] if q['case']<4 else [0x1234abcd]*12)
            expected.append((2,1,struct.pack('<12I',*words)))
        if q['count_arg']:
            expected.append((2,2,struct.pack('<I',1 if q['case']<4 else 0xdecafbad)))
        assert SD.blobs_of(trace,r['seq'])==expected, 'captured query blob'

def captured(run,rows):
    outer,logs,summary=cpu_observations(rows)
    audit_bytecode(run/'vs.bytecode',False);audit_bytecode(run/'ps.bytecode',True)
    trace=run/'trace.sa2s';assert trace.is_file() and (run/'counts.json').is_file() and Path(str(trace)+'.status').is_file()
    s=SD.scan(trace,want_range=(0,10000),keep_calls=1000)
    # The exact method sequence also catches a global rec_call or bypass before any reader problem is credited.
    calls=[r for r in s['range'] if r['kind']=='call']
    start=max(i for i,r in enumerate(calls) if r['type']==15 and r['slot']==9)+1
    end=next(i for i,r in enumerate(calls[start:],start) if r['type']==1 and r['slot'] in (95,97,99,110,112,114))
    core=calls[start:end]
    expected=[];at=0
    for slot,n in zip(OUTER_SLOTS,DELTAS,strict=True):
        expected.extend([(1,v) for v in DEVICE_SLOTS[at:at+n]]);expected.append((15,slot));at+=n
    assert [(r['type'],r['slot']) for r in core]==expected, 'captured core order/count'
    at=0;pos=0;logpos=0
    for o,n in zip(outer,DELTAS,strict=True):
        group=[];logpos+=1
        for k in range(n):
            r=core[pos];pos+=1
            args=[int(x,16) for x in r['args']]
            assert (int(r['this'],16),r['thread'],args[0],args[2],int(r['eax'],16)) == \
                   (summary['device'],0,REGISTERS[at],1,0), 'captured device ABI'
            assert args[1]==logs[logpos]['args'][1] and args[1]!=o['input'];logpos+=1
            assert SD.blobs_of(trace,r['seq']) == [(1,1,struct.pack('<'+'I'*len(PAYLOADS[at]),*PAYLOADS[at]))], 'captured literal blob'
            group.append(args[1]);at+=1
        assert len(set(group))==1
        r=core[pos];pos+=1
        assert int(r['this'],16)==o['table'] and r['thread']==0 and [int(x,16) for x in r['args']]==[summary['device'],o['handle'],o['input']]
        assert int(r['eax'],16)==0 and SD.blobs_of(trace,r['seq'])==[], 'captured outer table'
    captured_descriptors(trace,calls,rows)
    assert not s['problems'] and not s['notes'], s['problems']
    assert [r['kind'] for r in s['range']].count('end')==1
    assert s['range'][-1]['kind']=='end' and not any(r['kind']=='frame' for r in s['range'])
    counts=json.loads((run/'counts.json').read_text())
    assert SD.compare_counts(s,counts)==[]
    (run/'scan.json').write_text(json.dumps(s,indent=2)+'\n')

def test_nested_registered_capture_and_observational_equality(tmp_path):
    env=build(tmp_path)
    cp,on,run=execute(tmp_path,env,'on');assert cp.returncode==0,cp.stderr.decode()
    captured(run,on)
    cp,off,plain=execute(tmp_path,env,'off',False);assert cp.returncode==0,cp.stderr.decode()
    cpu_observations(off)
    assert on==off, 'record-on/off CPU/log/bank/registry/memory observations'
    assert not (plain/'trace.sa2s').exists() and not (plain/'counts.json').exists()

@pytest.mark.parametrize('control',['bypass','global','restore','blob','zero'])
def test_compiled_route_and_capture_falsifiers(tmp_path,control):
    env=build(tmp_path,control)
    cp,rows,run=execute(tmp_path,env,'mutant')
    if control=='restore':
        assert cp.returncode==1 and b'outer-record-settlement' in cp.stderr
        assert next(r for r in rows if r['event']=='summary')['helper_calls']==12
        assert [r['words'] for r in rows if r['event']=='bank']==PAYLOADS
    else:
        assert cp.returncode==0,cp.stderr.decode()
        with pytest.raises(AssertionError,match='captured core order/count' if control in ('bypass','global') else 'captured literal blob'):
            captured(run,rows)

@pytest.mark.parametrize('control',['desc-1-drop','desc-1-short','desc-2-drop','desc-2-short'])
def test_constant_descriptor_blob_falsifiers(tmp_path,control):
    env=build(tmp_path,control)
    cp,rows,run=execute(tmp_path,env,'mutant')
    assert cp.returncode==0,cp.stderr.decode()
    with pytest.raises(AssertionError,match='captured query blob'):
        captured(run,rows)
