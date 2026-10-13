"""Literal optimized CTAB descriptions through the real guest vtable."""
import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT=Path(__file__).resolve().parents[3]
ZIG=ROOT/'private/predecessor-tools/zig/zig-x86_64-linux-0.15.2/zig'
RUNTIME=ROOT/'runtime/sa2_d3d9.c'

@pytest.fixture(scope='module')
def binaries(tmp_path_factory):
    d=tmp_path_factory.mktemp('optimized-ctab')
    env=dict(os.environ,ZIG_LOCAL_CACHE_DIR=str(d/'local'),ZIG_GLOBAL_CACHE_DIR=str(d/'global'))
    source=RUNTIME.read_text()
    replacements={
        'unoptimized':('if (reg+count > 65536u ||','if (count < shape.regs || reg+count > 65536u ||'),
        'logical-index':('reg + consumed, allocated - consumed,','reg + i*one.regs, allocated - consumed,'),
        'short-default':('!table_mul(shape.regs,set == 0u','!table_mul(count,set == 0u'),
        'int-clamp':('set == 1u && !parent ? count : allocated','allocated'),
        'struct-index':('set, reg+consumed, allocated-consumed,','set, reg+logical, allocated-consumed,')}
    inputs={'current':RUNTIME}
    for name,(before,after) in replacements.items():
        assert source.count(before)==1
        path=d/f'{name}.c';path.write_text(source.replace(before,after,1));inputs[name]=path
    result={}
    for name,path in inputs.items():
        out=d/name
        argv=[str(ZIG),'cc','-target','x86_64-linux-musl','-O1','-Wall','-Wextra','-Werror',
            '-I',str(ROOT/'runtime'),str(ROOT/'harness/d3dx_optimized_probe.c'),
            str(path),str(ROOT/'runtime/sa2_guest_call.c'),'-o',str(out)]
        cp=subprocess.run(argv,capture_output=True,env=env)
        (d/f'compile-{name}.json').write_text(json.dumps({'argv':argv,'exit':cp.returncode})+'\n')
        (d/f'compile-{name}.stderr').write_bytes(cp.stderr)
        assert cp.returncode==0,cp.stderr.decode()
        result[name]=out
    return result

@pytest.fixture(scope='module')
def probe(binaries):
    return binaries['current']

def read(probe,kind,count,defaults='full'):
    cp=subprocess.run([str(probe),kind,str(count),defaults],capture_output=True,text=True)
    assert cp.returncode==0,cp.stderr+cp.stdout
    return [json.loads(line) for line in cp.stdout.splitlines()]

@pytest.mark.parametrize('count,allocated,indices,counts',[
    (5,5,[20,23,25],[3,2,0]),(0,0,[20,20,20],[0,0,0]),(13,9,[20,23,26],[3,3,3])])
def test_optimized_matrix_array(probe,count,allocated,indices,counts):
    rows=read(probe,'matrix',count)
    assert rows[0]=={'name':'value','count':1,'fields':[2,20,allocated,3,3,2,3,3,0,72],
                    'default_offset':0,'default_word':0x3f800000}
    for i,(r,index,n) in enumerate(zip(rows[1:],indices,counts,strict=True)):
        assert r=={'name':f'value[{i}]','count':1,'fields':[2,index,n,3,3,2,3,1,0,24],
                   'default_offset':i*48,'default_word':0x3f800000+i*12}

@pytest.mark.parametrize('kind,root,indices,counts,shape,stride',[
    ('float',1,[20,21],[1,0],[2,1,3,1,4,2,0,32],16),
    ('int',13,[20,21,22],[1,1,1],[1,1,2,1,3,3,0,36],16),
    ('bool',3,[20,21,22,23],[1,1,1,0],[0,0,1,1,1,4,0,16],4)])
def test_scalar_and_vector_array_registers(probe,kind,root,indices,counts,shape,stride):
    rows=read(probe,kind,root)
    s,cl,ty,nr,nc,el,members,total=shape
    assert rows[0]['fields']==[s,20,root,cl,ty,nr,nc,el,members,total]
    assert rows[0]['default_offset']==0 and rows[0]['default_word']==0x3f800000
    for i,(r,index,n) in enumerate(zip(rows[1:],indices,counts,strict=True)):
        assert r=={'name':f'value[{i}]','count':1,'fields':[s,index,n,cl,ty,nr,nc,1,0,nr*nc*4],
                   'default_offset':i*stride,'default_word':0x3f800000+i*stride//4}

def test_no_default_pointers(probe):
    rows=read(probe,'matrix',5,'none')
    assert [r['fields'][2] for r in rows]==[5,3,2,0]
    assert all(r['default_offset']==r['default_word']==0 for r in rows)

@pytest.mark.parametrize('count,root,indices,counts',[
    (9,9,[20,26,29],[6,3,0]),(0,0,[20,20,20],[0,0,0]),(21,18,[20,26,32],[6,6,6])])
def test_struct_register_cursor_and_logical_defaults(probe,count,root,indices,counts):
    rows=read(probe,'struct',count)
    assert rows[0]=={'name':'value','count':1,'fields':[2,20,root,5,0,1,1,3,3,12],
                    'default_offset':0,'default_word':0x3f800000}
    for i,(index,n) in enumerate(zip(indices,counts,strict=True)):
        r=rows[1+4*i]
        assert r=={'name':f'value[{i}]','count':1,'fields':[2,index,n,5,0,1,1,1,3,4],
                   'default_offset':i*96,'default_word':0x3f800000+i*24}
        member_counts=([1,1,n-2] if n else [0,0,0])
        for k,(name,cl,nr,nc,logical_bytes) in enumerate([('x',0,1,1,4),('y',1,1,3,12),('m',3,4,4,64)]):
            member_index=index+min(k,n)
            assert rows[2+4*i+k]=={'name':f'value[{i}].{name}','count':1,
                'fields':[2,member_index,member_counts[k],cl,3,nr,nc,1,0,logical_bytes],
                'default_offset':i*96+k*16,'default_word':0x3f800000+i*24+k*4}

def test_optimized_defaults_need_complete_logical_payload(probe):
    cp=subprocess.run([str(probe),'matrix','5','short'],capture_output=True,text=True)
    assert cp.returncode==2 and 'CTAB register/default extent is invalid' in cp.stderr
    assert not cp.stdout

def test_full_register_requirement_mutant_refuses_real_optimized_layout(binaries):
    cp=subprocess.run([str(binaries['unoptimized']),'matrix','5','full'],capture_output=True,text=True)
    assert cp.returncode==2 and 'CTAB register/default extent is invalid' in cp.stderr
    assert not cp.stdout

def test_logical_index_mutant_fails_literal_allocated_tail(binaries):
    rows=read(binaries['logical-index'],'matrix',5)
    assert [r['fields'][1] for r in rows]==[20,20,23,26]
    with pytest.raises(AssertionError):
        test_optimized_matrix_array(binaries['logical-index'],5,5,[20,23,25],[3,2,0])

def test_allocated_default_bound_mutant_accepts_truncated_logical_storage(binaries):
    cp=subprocess.run([str(binaries['short-default']),'matrix','5','short'],capture_output=True,text=True)
    assert cp.returncode==0 and cp.stderr==''
    assert len([json.loads(line) for line in cp.stdout.splitlines()])==4

def test_int_root_clamp_mutant_loses_literal_excess_count(binaries):
    rows=read(binaries['int-clamp'],'int',13)
    assert rows[0]['fields'][2]==3
    with pytest.raises(AssertionError):
        test_scalar_and_vector_array_registers(binaries['int-clamp'],'int',13,[20,21,22],[1,1,1],[1,1,2,1,3,3,0,36],16)

def test_struct_logical_index_mutant_moves_zero_tail_members(binaries):
    rows=read(binaries['struct-index'],'struct',9)
    assert [r['fields'][1] for r in rows[-3:]]==[29,30,31]
    with pytest.raises(AssertionError):
        test_struct_register_cursor_and_logical_defaults(binaries['struct-index'],9,9,[20,26,29],[6,3,0])
