"""Detached A0/B0 component measurements, never campaign/search-strategy tuning."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.run_seed import run_base,spawn_detached
from spire_exact.planning.io import read_json,write_json


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--workspace',type=Path,required=True)
    p.add_argument('--solver',type=Path,required=True)
    p.add_argument('--name',default='final-04')
    p.add_argument('--detach',action='store_true')
    p.add_argument('--resume',action='store_true')
    a=p.parse_args();folder=run_base('iteration-052')/a.name
    if a.detach:
        if not a.resume:folder.mkdir(parents=True,exist_ok=False)
        elif not folder.is_dir():raise ValueError('Cannot resume missing batch')
        command=[sys.executable,Path(__file__).resolve(),*[v for v in sys.argv[1:]if v!='--detach']]
        suffix='-resume'if a.resume else ''
        pid=spawn_detached(command,folder/('controller'+suffix+'.log'),ROOT)
        write_json(folder/('controller'+suffix+'.json'),{'pid':pid,'command':list(map(str,command))})
        print(json.dumps({'pid':pid,'out':str(folder)}));return
    from tools.fight_bench import _affinity,compare_exact
    from tools.audit_infra_bench import audit_pair
    from tools.rolling_storage import admission
    _affinity('e')
    state={'state':'running','started_unix':time.time(),'pid':os.getpid(),'steps':[],
           'scope':'18 retained component entries, Low / 10000 nodes / no gate escalation. E-core correctness and writes; no production speed conclusion.'}
    status=folder/'status.json';write_json(status,state)
    workspace=a.workspace.resolve()
    env=dict(os.environ,PYTHONIOENCODING='utf-8',NO_PROXY='localhost,127.0.0.1,::1')
    def run(name,command):
        step={'name':name,'state':'running','started_unix':time.time(),'command':list(map(str,command))}
        state['steps'].append(step);write_json(status,state)
        with (folder/(name+'.log')).open('x',encoding='utf-8') as log:
            proc=subprocess.Popen(list(map(str,command)),cwd=workspace,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
            step['pid']=proc.pid;write_json(status,state);code=proc.wait()
        step.update(state='finished',exit_code=code,finished_unix=time.time());write_json(status,state)
        if code:raise RuntimeError(name+' failed')
    try:
        state['storage']=admission(ROOT);write_json(status,state)
        audits=[]
        for seed in ('10101010','1741222413','564940356'):
            cases=run_base('iteration-052')/'resume-01'/(seed+'-cases.json')
            outputs={}
            for arm in ('original','off','writes','costs'):
                flag=arm in ('writes','costs')
                advisor={'measure_search_work':True,'measure_fork_writes':flag}
                if flag:advisor['fork_measurement_mode']=arm
                out=folder/(seed+'-'+arm);actual_cases=cases;step_name=seed+'-'+arm
                rows=[];failed=[]
                if a.resume and (out/'results.jsonl').is_file():
                    rows=[json.loads(line)for line in(out/'results.jsonl').read_text().splitlines()]
                    failed=[r for r in rows if r.get('error')or r.get('status')not in('BUDGET','DECISION','TERMINAL')]
                    if not failed:outputs[arm]=out;continue
                    if any(r.get('error')!='MEMORY_ADMISSION_DENIED'for r in failed):raise RuntimeError('Unresolved non-admission failure; do not blindly repeat')
                    document=read_json(cases);bad={r['case']for r in failed}
                    document['cases']=[c for c in document['cases']if c['id']in bad]
                    actual_cases=folder/(step_name+'-retry-cases.json');write_json(actual_cases,document)
                    step_name+='-retry';out=folder/step_name
                command=[sys.executable,workspace/'tools/run_fight_bench_job.py','--cases',actual_cases,'--out',out,
                         '--cpus','e','--workers','1','--runtime-profile','server-bounded-large-gen0','--worker-memory-mib','1536',
                         '--reserve-mib','0'if a.resume else '1024','--repeat','1','--gate-preset','none','--nodes','10000','--normal-nodes','10000',
                         '--advisor-json',json.dumps(advisor),'--timeout','1200']
                if arm!='original':command+=['--solver-dll',a.solver.resolve()]
                run(step_name,command)
                measured=[json.loads(line)for line in(out/'results.jsonl').read_text().splitlines()]
                if any(r.get('error')or r.get('status')not in('BUDGET','DECISION','TERMINAL')for r in measured):raise RuntimeError('Native request failure remains; retained original rows')
                if failed:
                    original=folder/(seed+'-'+arm);assembled=folder/(seed+'-'+arm+'-assembled');assembled.mkdir(exist_ok=False)
                    replacements={r['case']:(index,r)for index,r in enumerate(measured)};combined=[];sources=[]
                    for index,row in enumerate(rows):
                        replacement=replacements.get(row['case'])
                        source=(out/f'case-{replacement[0]:04d}-r0')if replacement else(original/f'case-{index:04d}-r0')
                        copied=assembled/f'case-{index:04d}-r0';shutil.copytree(source,copied,copy_function=os.link)
                        combined.append(replacement[1]if replacement else row);sources.append({'case':row['case'],'source':str(source),'copied_bytes_unchanged':True})
                    (assembled/'results.jsonl').write_text('\n'.join(json.dumps(r)for r in combined)+'\n')
                    write_json(assembled/'assembly.json',{'scope':'Original successful requests plus only failed admission retries; native files are unchanged NTFS hard links','sources':sources})
                    out=assembled
                outputs[arm]=out
            for left_arm,right_arm,change in (('original','off',True),('off','writes',False),('off','costs',False)):
                left,right=[outputs[arm]for arm in(left_arm,right_arm)]
                strict=compare_exact(left/'results.jsonl',right/'results.jsonl',allow_solver_change=change,allow_diagnostic_change=True)
                full=audit_pair(left,right,allow_solver_change=change,allow_diagnostic_change=True)
                item={'seed':seed,'arms':[left_arm,right_arm],'strict':strict,'full_export_audit':full};audits.append(item)
                write_json(folder/'native-equivalence.json',{'passed':all(r['strict']['equivalent']and r['full_export_audit']['passed']for r in audits),'pairs':audits})
                if not strict['equivalent']or not full['passed']:raise RuntimeError('Diagnostic native equivalence/clock/offline check failed')
        state.update(state='completed',finished_unix=time.time(),native_equivalence_passed=True,requests=72)
    except BaseException as error:
        state.update(state='needs_review',error=repr(error),finished_unix=time.time());write_json(status,state);raise
    write_json(status,state);print(json.dumps({'state':state['state'],'requests':72}))


if __name__=='__main__':main()
