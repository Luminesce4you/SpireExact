"""Paired fatal-entry rescue: node ceiling versus beam/inner-branch width.

Uses real DEV prefixes as component inputs, never fresh-run answers. A failed
probe is not infeasibility. No game rules or installed binaries are changed.
"""
from pathlib import Path
import argparse, collections, ctypes as c, json, os, subprocess, sys, time
from concurrent.futures import as_completed

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.planning.io import read_json,write_json
from spire_exact.planning.archive import failure_combat_prefix,classify_failure
from spire_exact.planning.pool import NativePool
from spire_exact.planning.resources import ResourcePlan
from spire_exact.canonical import canonical
from spire_exact.mode1 import is_winning_candidate,check_winning_replay
from tools.rolling_storage import active_seed
from tools.experiment import version_hash

ARMS={'low-60k':('Low',60000),'low-600k':('Low',600000),'wide-600k':('VeryHigh',600000)}

def select_cases(source):
    records=read_json(source/'result.json')['evaluations'];cases=[];seen=set()
    for floor in (17,33,48,49):
        bucket=sorted([r for r in records if r['classification']=='NATIVE_ROUTE_DEATH'
                       and (r.get('observation')or{}).get('floor')==floor
                       and r['kind']!='combat_probe'],key=lambda r:int(r['label'].split('-')[1]))
        if len(bucket)<3:raise ValueError('Not enough prespecified boss deaths')
        # Deterministic chronological strata; selection never inspects rescue outcomes.
        for position in (0,(len(bucket)-1)//2,len(bucket)-1):
            record=bucket[position];label=record['label'];trajectory=read_json(source/label/'data/decision.json')
            entry=failure_combat_prefix(trajectory)
            if entry is None:raise ValueError('Missing actual fatal combat entry')
            key=canonical(entry['prefix'])
            if key in seen:raise ValueError('Stratified inputs repeat the same complete entry prefix')
            seen.add(key)
            cases.append({'id':f'floor-{floor}-{len(cases):02}','floor':floor,'source_label':label,
                          'source_position':position,'eligible_count':len(bucket),'entry':entry,
                          'original_request':read_json(source/label/'request.json')})
    return cases

def event(out,kind,**fields):
    row={'event':kind,'time':time.time(),**fields}
    with(out/'events.jsonl').open('a',encoding='utf-8')as f:f.write(json.dumps(row)+'\n')
    print(json.dumps(row),flush=True)

def run_cases(source,out):
    cases=select_cases(source)
    write_json(out/'selection.json',[{k:v for k,v in case.items()if k not in ('original_request','entry')}|
                                   {'entry_prefix_length':len(case['entry']['prefix'])}for case in cases])
    data=ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
    plan=ResourcePlan.detect(4,1,2048,2048)
    if plan.workers!=4:raise ValueError('Four prespecified workers cannot fit')
    rows=[]
    with NativePool(data,out/'workers',plan)as pool:
        pending={}
        for i,case in enumerate(cases):
            for arm in (list(ARMS)if i%2==0 else list(ARMS)[::-1]):
                profile,nodes=ARMS[arm];original=case['original_request'];entry=case['entry']
                req={k:original[k]for k in ('seed','character','ascension','unlocks')}
                advisor=dict(original['advisor'],profile=profile,nodes=nodes,dop=1,budget_ms=600000,boss_budget_ms=600000)
                advisor.pop('beam',None)
                req.update(history=entry['prefix'],generate_candidate=True,advisor=advisor,
                           policy_seed=original['policy_seed'],capture_checkpoints=False,low_io=True,event_driven_settle=True,
                           max_decisions=len(entry['prefix'])+1500,stop_at_floor=case['floor'])
                target=out/(case['id']+'-'+arm)
                pending[pool.submit(req,target,1200)]=(case,arm,req,target)
        for future in as_completed(pending):
            case,arm,req,target=pending[future]
            row={'case':case['id'],'floor':case['floor'],'arm':arm,'source_label':case['source_label'],
                 'native_entry_verified':False,'rescued':False,'independent_replay_verified':False}
            try:
                result,identity=future.result()
                prefix=req['history'];trace=result.get('trace',[]);evidence=result.get('decision_evidence',[])
                entry_ok=(len(trace)>len(prefix) and canonical(trace[:len(prefix)])==canonical(prefix)
                          and canonical(evidence[len(prefix)]['observation'])==canonical(case['entry']['entry_observation']))
                searches=(result.get('advisor_metrics')or{}).get('searches',[])
                obs=result.get('observation')or{};reason=result.get('reason')
                crossed=any((e.get('observation')or{}).get('floor',0)>case['floor']for e in evidence[len(prefix):])
                rescued=entry_ok and (is_winning_candidate(result) or crossed or
                    (result.get('status')in('BUDGET','DECISION') and reason=='candidate_horizon'
                     and float(obs.get('hp',0))>0))
                row.update(classification=classify_failure(result),reason=reason,native_entry_verified=entry_ok,
                    rescued=rescued,terminal_floor=obs.get('floor'),hp=obs.get('hp'),searches=searches,
                    performance=result.get('performance'),terminal_combat=result.get('terminal_combat'))
                if rescued:
                    replay_request={k:req[k]for k in ('seed','character','ascension','unlocks')}
                    replay_request.update(history=trace,generate_candidate=False,capture_checkpoints=False,
                                          expected_evidence=evidence,low_io=True,event_driven_settle=True)
                    replay,other=pool.run(replay_request,out/('verify-'+target.name),600,fresh=True)
                    row['independent_replay_verified']=(canonical(replay['trace'])==canonical(trace)
                        and canonical(replay['decision_evidence'])==canonical(evidence)
                        and canonical(replay['observation'])==canonical(result['observation'])
                        and canonical(identity)==canonical(other))
                    if not row['independent_replay_verified']:row['rescued']=False;row['verification_error']='REPLAY_DIVERGENCE'
                    if is_winning_candidate(result) and row['independent_replay_verified']:
                        from spire_exact.mode1 import context
                        ctx=context(req['seed'],req['character'],req['ascension'],req['unlocks'])
                        cert=check_winning_replay(result,replay,{'context':ctx,'native':identity},{'context':ctx,'native':other})
                        write_json(out/('certificate-'+target.name+'.json'),cert)
            except Exception as error:
                row.update(classification='DIAGNOSTIC_ERROR',error=str(error),rescued=False)
            rows.append(row)
            write_json(out/'partial-results.json',{'complete':False,'rows':rows})
            event(out,'tactical_case_completed',**{k:row[k]for k in ('case','arm','classification','rescued','independent_replay_verified')})
    summaries={arm:{'completed':sum(r['arm']==arm for r in rows),'rescued':sum(r['arm']==arm and r['rescued']for r in rows),
               'entry_verified':sum(r['arm']==arm and r['native_entry_verified']for r in rows),
               'expanded_nodes':sum(sum(s.get('expanded_nodes',0)for s in r.get('searches',[]))for r in rows if r['arm']==arm)}for arm in ARMS}
    report={'complete':True,'rows':rows,'summary':summaries,'counts_as_fresh_win':False,
            'scope':'12 correlated DEV fatal entries; diagnostic interventions, not a generalization test',
            'not_infeasibility_evidence':True}
    write_json(out/'report.json',report);event(out,'tactical_diagnosis_completed',summary=summaries)

def main():
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--wait-for-pid',type=int);p.add_argument('--smt-report',type=Path)
    p.add_argument('--previous-experiment-stopped',action='store_true');p.add_argument('--child',action='store_true');a=p.parse_args()
    a.out=a.out.resolve();a.source=a.source.resolve()
    if a.child:
        from tools import limited_cli
        sys.argv=[__file__,'--out',str(a.out/'cases')]
        limited_cli.runpy.run_module=lambda *args,**kwargs:run_cases(a.source,a.out/'cases')
        limited_cli.main();return
    a.out.mkdir(parents=True,exist_ok=False)
    if a.wait_for_pid:
        k=c.WinDLL('kernel32',use_last_error=True);k.OpenProcess.argtypes=[c.c_ulong,c.c_int,c.c_ulong];k.OpenProcess.restype=c.c_void_p
        k.WaitForSingleObject.argtypes=[c.c_void_p,c.c_ulong];k.CloseHandle.argtypes=[c.c_void_p]
        h=k.OpenProcess(0x100000,False,a.wait_for_pid)
        if not h:raise c.WinError(c.get_last_error())
        event(a.out,'waiting_for_smt_exit',pid=a.wait_for_pid)
        try:
            if k.WaitForSingleObject(h,0xffffffff)!=0:raise c.WinError(c.get_last_error())
        finally:k.CloseHandle(h)
    if a.smt_report:
        gate=read_json(a.smt_report)
        if not gate.get('complete')or not gate.get('passed'):raise RuntimeError('SMT result identity gate not passed')
    elif not a.previous_experiment_stopped:
        raise ValueError('Require a completed SMT gate or the explicitly requested stopped-experiment handoff')
    write_json(a.out/'validation-manifest.json',{'run_id':a.out.name,'version':version_hash(),'source':str(a.source),
        'protocol':'A10-seed-v2','stage':'M0 L1 diagnostic','tactical_arms':ARMS,'counts_as_fresh_win':False,
        'selection':'first, median, last chronological non-probe death at floors 17,33,48,49; exact prefixes distinct',
        'workers':4,'worker_mib':2048,'workload_bytes':14*1024**3,'os_reserve_bytes':2*1024**3,
        'solver_dop':1,'runtime_profile':'legacy','wall_cap_seconds':7200,'per_task_seconds':1200,
        'smt_gate':str(a.smt_report)if a.smt_report else None,
        'user_requested_stop_smt_and_start_diagnostic':a.previous_experiment_stopped,'normal_godot_verified':False})
    cmd=[sys.executable,__file__,'--source',str(a.source),'--out',str(a.out),'--child']
    env=dict(os.environ,SPIRE_PROTOCOL='A10-seed-v2',SPIRE_WALL_LIMIT_SECONDS='7200',PYTHONIOENCODING='utf-8')
    with active_seed(a.out/'cases'),subprocess.Popen(cmd,cwd=ROOT,env=env)as proc:
        write_json(a.out/'process.json',{'coordinator_pid':os.getpid(),'owned_job_pid':proc.pid})
        event(a.out,'tactical_diagnosis_started',pid=proc.pid);code=proc.wait()
    event(a.out,'tactical_process_finished',exit_code=code)
    raise SystemExit(code)

if __name__=='__main__':main()
