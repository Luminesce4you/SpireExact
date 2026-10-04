"""Finish full copy-write/fate coverage with the fixed diagnostic build."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.run_seed import run_base,spawn_detached
from spire_exact.planning.io import read_json,write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--workspace',type=Path,required=True);p.add_argument('--solver',type=Path,required=True);p.add_argument('--name',default='final-07');p.add_argument('--detach',action='store_true');p.add_argument('--resume',action='store_true');a=p.parse_args()
    a.workspace=a.workspace.resolve();a.solver=a.solver.resolve()
    folder=run_base('iteration-052')/a.name
    if a.detach:
        if not a.resume:folder.mkdir(exist_ok=False)
        command=[sys.executable,Path(__file__).resolve(),*[v for v in sys.argv[1:]if v!='--detach']]
        label='controller-resume'if a.resume else 'controller'
        pid=spawn_detached(command,folder/(label+'.log'),ROOT);write_json(folder/(label+'.json'),{'pid':pid,'command':list(map(str,command))});print(json.dumps({'pid':pid,'out':str(folder)}));return
    from tools.fight_bench import _affinity,compare_exact
    from tools.audit_infra_bench import audit_pair
    from tools.analyze_copy_diagnostics import checked_search
    _affinity('e');state={'state':'running','started_unix':time.time(),'pid':os.getpid(),'steps':[]};status=folder/'status.json';write_json(status,state)
    env=dict(os.environ,PYTHONIOENCODING='utf-8',NO_PROXY='localhost,127.0.0.1,::1');audits=[];accounting=[]
    def run(name,command):
        step={'name':name,'state':'running','started_unix':time.time(),'command':list(map(str,command))};state['steps'].append(step);write_json(status,state)
        with(folder/(name+'.log')).open('x',encoding='utf-8')as log:
            process=subprocess.Popen(list(map(str,command)),cwd=a.workspace.resolve(),env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
            step['pid']=process.pid;write_json(status,state);code=process.wait()
        step.update(state='finished',exit_code=code,finished_unix=time.time());write_json(status,state)
        if code:raise RuntimeError(name+' failed')
    try:
        for seed in('10101010','1741222413','564940356'):
            cases=run_base('iteration-052')/'resume-01'/(seed+'-cases.json')
            for arm in('original','off','writes','costs'):
                flag=arm in('writes','costs')
                advisor={'measure_search_work':True,'measure_fork_writes':flag}
                if flag:advisor['fork_measurement_mode']=arm
                out=folder/(seed+'-'+arm)
                command=[sys.executable,a.workspace/'tools/run_fight_bench_job.py','--cases',cases,'--out',out,'--cpus','e','--workers','1',
                    '--runtime-profile','server-bounded-large-gen0','--worker-memory-mib','1536','--reserve-mib','0','--repeat','1',
                    '--gate-preset','none','--nodes','10000','--normal-nodes','10000',
                    '--advisor-json',json.dumps(advisor),'--timeout','1200']
                if arm!='original':command+=['--solver-dll',a.solver]
                if not(a.resume and (out/'results.jsonl').is_file()):run(seed+'-'+arm,command)
                rows=[json.loads(line)for line in(out/'results.jsonl').read_text().splitlines()]
                if len(rows)!=6 or any(r.get('error')or r['status']not in('BUDGET','DECISION','TERMINAL')for r in rows):raise RuntimeError('Failed/absent request, not a measurement')
                if flag:
                    for path in sorted(out.glob('case-*/data/decision.json*')):
                        for index,search in enumerate(read_json(path)['advisor_metrics']['searches']):accounting.append({'path':str(path),'index':index,'checks':checked_search(search)})
                write_json(folder/'accounting-checks.json',accounting)
            for left,right,change in((folder/(seed+'-original'),folder/(seed+'-off'),True),
                (folder/(seed+'-off'),folder/(seed+'-writes'),False),(folder/(seed+'-off'),folder/(seed+'-costs'),False)):
                strict=compare_exact(left/'results.jsonl',right/'results.jsonl',allow_solver_change=change,allow_diagnostic_change=True)
                full=audit_pair(left,right,allow_solver_change=change,allow_diagnostic_change=True)
                audits.append({'seed':seed,'left':str(left),'right':str(right),'strict':strict,'full_export_audit':full})
                write_json(folder/'native-equivalence.json',{'passed':all(r['strict']['equivalent']and r['full_export_audit']['passed']for r in audits),'pairs':audits})
                if not strict['equivalent']or not full['passed']:raise RuntimeError('Game/request/node/clock/offline mismatch')
        state.update(state='completed',finished_unix=time.time(),native_requests=72,native_equivalence_passed=True,accounting_passed=True)
    except BaseException as error:
        state.update(state='needs_review',error=repr(error),finished_unix=time.time());write_json(status,state);raise
    write_json(status,state);print(json.dumps({'state':'completed','native_requests':72,'accounting_passed':True}))


if __name__=='__main__':main()
