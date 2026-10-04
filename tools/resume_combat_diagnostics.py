"""Detached E-core A0 native equivalence and retained B0 stack analysis; no speed claim."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.run_seed import run_base,spawn_detached
from spire_exact.planning.io import read_json,write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--workspace',type=Path,required=True);p.add_argument('--detach',action='store_true');a=p.parse_args()
    folder=run_base('iteration-052')/'resume-01'
    if a.detach:
        folder.mkdir(parents=True,exist_ok=False)
        command=[sys.executable,Path(__file__).resolve(),*[v for v in sys.argv[1:]if v!='--detach']]
        pid=spawn_detached(command,folder/'controller.log',ROOT)
        write_json(folder/'controller.json',{'detached_parent_pid':pid,'command':list(map(str,command))})
        print(json.dumps({'detached_pid':pid,'out':str(folder)}));return
    kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.GetCurrentProcess.restype=ctypes.c_void_p
    kernel.SetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
    if not kernel.SetProcessAffinityMask(kernel.GetCurrentProcess(),(1<<28)|(1<<29)):raise ctypes.WinError()
    state={'state':'running','controller_pid':os.getpid(),'started_unix':time.time(),'steps':[],
           'scope':'E-core diagnostics and equivalence only; B0 write coverage and A1/A2 remain outstanding'}
    status=folder/'status.json';write_json(status,state)
    workspace=a.workspace.resolve()
    env=dict(os.environ,PYTHONIOENCODING='utf-8',NO_PROXY='localhost,127.0.0.1,::1')
    def run(name,command):
        step={'name':name,'state':'running','started_unix':time.time(),'command':list(map(str,command))}
        state['steps'].append(step);write_json(status,state)
        with (folder/(name+'.log')).open('x',encoding='utf-8')as stream:
            proc=subprocess.Popen(list(map(str,command)),cwd=workspace,env=env,stdout=stream,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
            step['pid']=proc.pid;write_json(status,state);code=proc.wait()
        step.update(state='finished',exit_code=code,finished_unix=time.time());write_json(status,state)
        if code:raise RuntimeError(name+' failed; retained log identifies the boundary')
    try:
        from tools.rolling_storage import admission
        state['storage']=admission(ROOT);write_json(status,state)
        artifact=run_base('iteration-042')
        for seed in('1741222413','564940356'):
            run('profile-'+seed,[sys.executable,workspace/'tools/profile_fork_costs.py',artifact/'profiles'/seed/'warm.speedscope.json',
                                '--out',folder/('fork-profile-'+seed+'.json')])
        from tools.fight_bench import compare_exact
        from tools.audit_infra_bench import audit_pair
        audits=[]
        for seed in('10101010','1741222413','564940356'):
            document=read_json(ROOT/f'experiments/iteration-042/cases-{seed}.json')
            selected=[]
            for room in('Monster','Elite','Boss'):
                selected.extend([case for case in document['cases']if case['room']==room][:2])
            if len(selected)!=6:raise ValueError('Require two retained entries for every room')
            document['cases']=selected
            document['diagnostic_scope']='Small-budget counter-export sentinel; not an A1 quality set. DEV seed here is only an implementation diagnostic.'
            cases=folder/(seed+'-cases.json');write_json(cases,document)
            for label,flag in(('off',False),('on',True)):
                advisor={'measure_search_work':flag}
                command=[sys.executable,workspace/'tools/run_fight_bench_job.py','--cases',cases,'--out',folder/(seed+'-'+label),
                         '--cpus','e','--workers','3','--runtime-profile','server-bounded-large-gen0','--worker-memory-mib','1024',
                         '--reserve-mib','1024','--repeat','2','--gate-preset','none','--nodes','10000','--normal-nodes','10000',
                         '--advisor-json',json.dumps(advisor),'--timeout','1200']
                run(seed+'-'+label,command)
            left,right=folder/(seed+'-off'),folder/(seed+'-on')
            strict=compare_exact(left/'results.jsonl',right/'results.jsonl',allow_diagnostic_change=True)
            full=audit_pair(left,right,allow_diagnostic_change=True)
            item={'seed':seed,'strict':strict,'full_export_audit':full};audits.append(item)
            write_json(folder/'a0-native-equivalence.json',{'passed':all(r['strict']['equivalent']and r['full_export_audit']['passed']for r in audits),'seeds':audits})
            if not strict['equivalent']or not full['passed']:raise RuntimeError('A0 native equivalence or clock/offline audit failed')
        state.update(state='completed',finished_unix=time.time(),a0_native_sentinel_passed=True,
                     b0_retained_profiles_analyzed=3,remaining=['A0 production fate accounting','B0 true write instrumentation and U_lazy/U_undo','A1','A2'])
    except BaseException as error:
        state.update(state='needs_review',error=repr(error),finished_unix=time.time());write_json(status,state);raise
    write_json(status,state);write_json(ROOT/'experiments/iteration-052/resume-status.json',state)
    print(json.dumps(state,ensure_ascii=False))


if __name__=='__main__':main()
