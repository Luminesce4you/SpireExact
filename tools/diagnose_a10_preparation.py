"""M0 L2 attribution using scoped existing search, not a new production policy."""
from pathlib import Path
import argparse,datetime,json,os,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.diagnose_a10_tactics import select_cases
from tools.experiment import version_hash
from tools.rolling_storage import active_seed
from spire_exact.canonical import canonical
from spire_exact.mode1 import context,is_winning_candidate,check_winning_replay
from spire_exact.planning.io import read_json,write_json
from spire_exact.planning.search import solve,SearchConfig,Evaluator
from spire_exact.planning.resources import ResourcePlan
from spire_exact.planning.pool import NativePool

def roots_from_cases(source):
    roots={}
    for case in select_cases(source):
        trajectory=read_json(source/case['source_label']/'data/decision.json')
        act=case['entry']['entry_observation']['act']
        index=next(i for i,e in enumerate(trajectory['decision_evidence'])if(e.get('observation')or{}).get('act')==act)
        prefix=trajectory['trace'][:index]
        key=(canonical(prefix),case['floor'])
        if key not in roots:
            roots[key]={'id':f"act-{act+1}-target-{case['floor']}-root-{len(roots):02}",
                        'act':act,'target_floor':case['floor'],'prefix':prefix,
                        'entry_observation':trajectory['decision_evidence'][index]['observation'],
                        'original_request':case['original_request'],'source_cases':[]}
        roots[key]['source_cases'].append(case['source_label'])
    return list(roots.values())

def event(out,kind,**values):
    row={'event':kind,'time':time.time(),**values}
    with(out/'events.jsonl').open('a',encoding='utf-8')as f:f.write(json.dumps(row)+'\n')
    print(json.dumps(row),flush=True)

def rescued(result,target):
    obs=result.get('observation')or{}
    # This diagnostic's floor 49 is the final second boss. Its post-boss map
    # still requires the native end-run transition; a live boundary is not a win.
    if target==49:return is_winning_candidate(result)
    return is_winning_candidate(result) or (result.get('reason')=='candidate_horizon'
        and result.get('phase')=='map' and obs.get('floor',-1)>=target and float(obs.get('hp')or 0)>0)

def candidate_artifact(directory):
    if (directory/'cached.json').exists():
        cached=read_json(directory/'cached.json')
        return cached['result'],cached['identity']
    return read_json(directory/'data/decision.json'),read_json(directory/'data/identity.json')

def run(source,out,evaluations,max_roots,repeats):
    roots=roots_from_cases(source)
    if max_roots:roots=roots[:max_roots]
    original=read_json(source/'result.json')
    config=SearchConfig(evaluations=evaluations,seconds=6900,task_seconds=1200,max_decisions=12000,
        lookahead_actions=12000,lookahead_floors=99,alternatives=8,survivors=2,archive_entries=256,
        checkpoint_mib=512,cache_mib=64,dispatch_mode='ordered',dispatch_window=6,
        solver_seed=original['configuration']['solver_seed'],low_io=True,event_driven_settle=True)
    plan=ResourcePlan.detect(6,1,1536,2048)
    if plan.workers!=6:raise ValueError('Prespecified six workers cannot fit')
    data=ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
    write_json(out/'roots.json',[{k:v for k,v in root.items()if k not in('prefix','original_request','entry_observation')}|
                                   {'prefix_length':len(root['prefix'])}for root in roots])
    rows=[]
    with NativePool(data,out/'workers',plan)as pool:
        for repeat in range(repeats):
            for root in roots:
                target=out/(root['id']+f'-repeat-{repeat}');request=root['original_request']
                ctx=context(request['seed'],request['character'],request['ascension'],request['unlocks'])
                advisor=dict(request['advisor'],profile='Low',nodes=60000,dop=1,budget_ms=600000,boss_budget_ms=600000)
                advisor.pop('beam',None)
                if Evaluator(None,ctx,target,config).request(root['prefix'])['policy_seed']!=request['policy_seed']:
                    raise ValueError('Baseline policy seed mapping differs from selected trajectory')
                event(out,'preparation_root_started',root=root['id'],repeat=repeat,target_floor=root['target_floor'])
                start=time.perf_counter()
                result=solve(ctx,target,pool,config,advisor=advisor,initial_prefix=root['prefix'],
                    scope_prefix=root['prefix'],stop_floor=root['target_floor']if root['target_floor']<49 else None)
                checked=0;first=None;errors=[];first_replay=None
                for record in sorted(result['evaluations'],key=lambda r:int(r['label'].split('-')[1])):
                    if not record.get('actions') and not record.get('cache_hit'):
                        errors.append({'label':record['label'],'classification':record['classification'],'reason':record.get('reason')});continue
                    candidate_dir=target/record['label']
                    value,original_identity=candidate_artifact(candidate_dir)
                    n=len(root['prefix'])
                    if canonical(value['trace'][:n])!=canonical(root['prefix']):raise ValueError('Candidate modified earlier-act prefix')
                    if len(value['decision_evidence'])<=n or canonical(value['decision_evidence'][n]['observation'])!=canonical(root['entry_observation']):
                        raise ValueError('Native act-entry observation differs from original source')
                    checked+=1
                    if first is None and rescued(value,root['target_floor']):
                        req={k:request[k]for k in('seed','character','ascension','unlocks')}
                        req.update(history=value['trace'],generate_candidate=False,capture_checkpoints=False,
                                   expected_evidence=value['decision_evidence'],low_io=True,event_driven_settle=True)
                        replay,identity=pool.run(req,target/('independent-'+record['label']),600,fresh=True)
                        same=(canonical(value['trace'])==canonical(replay['trace'])
                            and canonical(value['decision_evidence'])==canonical(replay['decision_evidence'])
                            and canonical(value['observation'])==canonical(replay['observation'])
                            and canonical(original_identity)==canonical(identity))
                        if not same:raise ValueError('Independent preparation rescue replay diverged')
                        if is_winning_candidate(value):
                            certificate=check_winning_replay(value,replay,{'context':ctx,'native':original_identity},{'context':ctx,'native':identity})
                            write_json(target/'gate-whole-run-certificate.json',certificate)
                        first={k:record.get(k)for k in('label','kind','completed_wall_seconds','expanded_combat_nodes')}
                        first_replay={'full_evidence_equal':same,'actions':len(replay['trace']),
                                      'whole_run_win':is_winning_candidate(replay),'fresh_native_pid':replay['performance']['pid']}
                row={'root':root['id'],'repeat':repeat,'source_cases':root['source_cases'],'act':root['act'],
                     'target_floor':root['target_floor'],'evaluations':len(result['evaluations']),
                     'entry_checked_candidates':checked,'errors':errors,'first_rescue':first,'independent_replay':first_replay,
                     'wall_seconds':time.perf_counter()-start,'search_metrics':result['search_metrics'],
                     'counts_as_fresh_planner_win':False,'scope_prefix_length':len(root['prefix'])}
                rows.append(row);write_json(out/'partial-results.json',{'complete':False,'rows':rows})
                event(out,'preparation_root_completed',root=root['id'],repeat=repeat,rescued=first is not None,
                      evaluations=row['evaluations'],entry_checked_candidates=checked,errors=len(errors))
    repeat_checks=[]
    if repeats>1:
        keys=['status','phase','reason','value','observation','trace','decision_evidence','native_terminal_observed']
        for root in roots:
            reference=out/(root['id']+'-repeat-0');a=read_json(reference/'result.json')['evaluations']
            for repeat in range(1,repeats):
                other=out/(root['id']+f'-repeat-{repeat}');b=read_json(other/'result.json')['evaluations']
                equal=[r['label']for r in a]==[r['label']for r in b]
                for x,y in zip(a,b):
                    if (not x.get('actions')and not x.get('cache_hit'))or(not y.get('actions')and not y.get('cache_hit')):equal=False;continue
                    l,_=candidate_artifact(reference/x['label']);r,_=candidate_artifact(other/y['label'])
                    equal &= canonical({k:l.get(k)for k in keys})==canonical({k:r.get(k)for k in keys})
                repeat_checks.append({'root':root['id'],'repeat':repeat,'full_game_bytes_equal':equal})
    report={'complete':True,'rows':rows,'root_count':len(roots),'rescued_roots':sum(r['first_rescue']is not None for r in rows),
            'repeats':repeats,'repeat_checks':repeat_checks,'counts_as_fresh_planner_win':False,
            'scope':'M0 L2 correlated DEV act-prefix diagnostic, not production validation or infeasibility proof'}
    write_json(out/'report.json',report);event(out,'preparation_diagnosis_completed',roots=len(roots),rescued=report['rescued_roots'])

def main():
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--evaluations',type=int,default=48);p.add_argument('--max-roots',type=int);p.add_argument('--repeats',type=int,default=1)
    p.add_argument('--preflight',type=Path);p.add_argument('--child',action='store_true');a=p.parse_args()
    a.out=a.out.resolve();a.source=a.source.resolve()
    if a.child:
        from tools import limited_cli
        sys.argv=[__file__,'--out',str(a.out/'cases')]
        limited_cli.runpy.run_module=lambda *args,**kwargs:run(a.source,a.out/'cases',a.evaluations,a.max_roots,a.repeats)
        limited_cli.main();return
    if a.preflight:
        gate=read_json(a.preflight)
        if not gate.get('complete')or not gate.get('repeat_checks')or not all(r['full_game_bytes_equal']for r in gate['repeat_checks']):
            raise ValueError('Scoped native repeatability preflight failed')
    elif a.evaluations>8:raise ValueError('Native scoped preflight required before full diagnostic')
    a.out.mkdir(parents=True,exist_ok=False)
    manifest={'run_id':a.out.name,'timestamp':datetime.datetime.now().astimezone().isoformat(),'protocol':'A10-seed-v2',
        'stage':'M0 L2 preparation diagnostic','version':version_hash(),'source':str(a.source),'counts_as_fresh_win':False,
        'settings':['--workers','6','--dop','1','--ascension','10','--nodes','60000'],
        'evaluations_per_root':a.evaluations,'max_roots':a.max_roots,'repeats':a.repeats,
        'workload_bytes':14*1024**3,'os_reserve_bytes':2*1024**3,'wall_cap_seconds':7200,
        'preflight':str(a.preflight)if a.preflight else None,'normal_godot_verified':False}
    write_json(a.out/'validation-manifest.json',manifest)
    cmd=[sys.executable,__file__,'--source',str(a.source),'--out',str(a.out),'--evaluations',str(a.evaluations),'--repeats',str(a.repeats),'--child']
    if a.max_roots:cmd+=['--max-roots',str(a.max_roots)]
    env=dict(os.environ,SPIRE_PROTOCOL='A10-seed-v2',SPIRE_WALL_LIMIT_SECONDS='7200',PYTHONIOENCODING='utf-8')
    with active_seed(a.out/'cases'),subprocess.Popen(cmd,cwd=ROOT,env=env)as proc:
        write_json(a.out/'process.json',{'coordinator_pid':os.getpid(),'owned_job_pid':proc.pid})
        event(a.out,'preparation_diagnosis_started',pid=proc.pid);code=proc.wait()
    event(a.out,'preparation_process_finished',exit_code=code)
    raise SystemExit(code)

if __name__=='__main__':main()
