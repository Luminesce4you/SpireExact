"""Actual bundled STS2 DLL integration suite, NOT synthetic-game evidence.

Tests checkpoint equality plus complete suffix replay, owned worker reuse,
invalid-context/tamper rejection, native action comparisons and character smoke.
Normal Godot scene parity remains explicitly unverified.
"""
import argparse,copy,json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.native import game_data
from spire_exact.canonical import canonical
from spire_exact.mode1 import context,NativeCampaignBackend
from spire_exact.planning.io import read_json,write_json
from spire_exact.planning.resources import ResourcePlan
from spire_exact.planning.pool import NativePool
from spire_exact.planning.archive import decision_groups,classify_failure,utility
from spire_exact.planning.search import Evaluator,SearchConfig,solve

def main():
    p=argparse.ArgumentParser();p.add_argument('--game-dir',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--workers',type=int,default=2);p.add_argument('--worker-memory-mib',type=int,default=800)
    p.add_argument('--planner-evaluations',type=int,default=0)
    p.add_argument('--event-driven-settle',action='store_true')
    a=p.parse_args();a.out=a.out.resolve()
    if a.out.exists() and any(a.out.iterdir()):p.error('use a fresh output directory')
    a.out.mkdir(parents=True,exist_ok=True)
    source=read_json(ROOT/'outputs/mode1-independent-prefix-check/request.json')
    ctx=context(source['seed'],source['character'],source['ascension'],source['unlocks'])
    request={k:source[k] for k in ('seed','character','ascension','unlocks','history')}
    request.update(generate_candidate=False,capture_checkpoints=True)
    if a.event_driven_settle:request['event_driven_settle']=True
    plan=ResourcePlan.detect(a.workers,1,a.worker_memory_mib)
    report={'scope':'actual supplied game DLLs in patched offline TestMode','normal_godot_parity':False,
        'resources':plan.as_dict(),'checks':[],'successful':False,'whole_run_verified':False}
    def check(name,passed,**details):
        report['checks'].append({'name':name,'passed':bool(passed),**details})
        write_json(a.out/'report.json',report);print(name,passed,flush=True)
    with NativePool(game_data(a.game_dir),a.out/'workers',plan) as pool:
        base,identity=pool.run(request,a.out/'capture',90)
        check('original_300_action_prefix',base['status']=='DECISION' and base['consumed']==300
            and base['observation']['act']==1,phase=base['phase'],floor=base['observation']['floor'])
        check('prefix_never_searches',base['performance']['counters']['replay_prefix_solver_calls']==0)
        check('checkpoints_captured',len(base['checkpoints'])>=4,count=len(base['checkpoints']))
        indices=sorted(set([min(4,len(base['checkpoints'])-1),min(10,len(base['checkpoints'])-1),len(base['checkpoints'])-2,len(base['checkpoints'])-1]))
        futures=[]
        for index in indices:
            cp=base['checkpoints'][index]
            req={**request,'checkpoint':cp['path'],'expected_evidence':base['decision_evidence']}
            futures.append((cp,pool.submit(req,a.out/f'suffix-{cp["prefix_length"]}',90)))
        for cp,future in futures:
            result,_=future.result()
            same=(result['status']=='DECISION' and result['restore_menu_checked'] and
                canonical(result['decision_evidence'])==canonical(base['decision_evidence']) and
                canonical(result['observation'])==canonical(base['observation']))
            check('suffix_from_'+str(cp['prefix_length']),same,restored_prefix=result['restored_prefix'],
                wall_us=result['performance']['wall_us'],pid=result['performance']['pid'],reason=result.get('reason'))
        # Same process is allowed to serve multiple jobs; each must reconstruct the same result.
        last=base['checkpoints'][-1]
        repeated=[]
        for i in range(3):
            result,_=pool.run({**request,'checkpoint':last['path'],'capture_checkpoints':False},a.out/f'repeat-{i}',45)
            repeated.append(result)
        check('repeated_restore_identical',all(canonical(d['observation'])==canonical(base['observation']) and d['restore_menu_checked'] for d in repeated),
              pids=[d['performance']['pid'] for d in repeated])
        tampered=read_json(last['path']);tampered['payload']['context']['seed']='CORRUPT'
        tamper_path=a.out/'tampered.json';write_json(tamper_path,tampered)
        result,_=pool.run({**request,'checkpoint':str(tamper_path),'capture_checkpoints':False},a.out/'reject-tamper',45)
        check('checksum_tamper_rejected',result['status']=='UNSUPPORTED' and 'CHECKPOINT_CHECKSUM' in result['reason'])
        result,_=pool.run({**request,'checkpoint':last['path'],'ascension':1,'capture_checkpoints':False},a.out/'reject-context',45)
        check('ascension_mismatch_rejected',result['status']=='UNSUPPORTED' and 'CHECKPOINT_MISMATCH:context' in result['reason'])
        bad=copy.deepcopy(base['decision_evidence']);bad[0]['phase']='WRONG'
        result,_=pool.run({**request,'expected_evidence':bad,'capture_checkpoints':False},a.out/'reject-evidence',45)
        check('replay_mismatch_rejected',result['status']=='UNSUPPORTED' and 'replay_trajectory' in result['reason'])
        # Dynamic native interfaces on several roles/seeds/difficulties. No win-rate inference.
        characters=['IRONCLAD','SILENT','DEFECT','REGENT','NECROBINDER']
        smoke=[]
        for i,character in enumerate(characters):
            req={k:request[k] for k in ('seed','character','ascension','unlocks')}
            req.update(seed=str(100+i),character=character,ascension=i%2,history=[],
                generate_candidate=True,max_decisions=24,policy_seed=0,capture_checkpoints=False)
            if a.event_driven_settle:req['event_driven_settle']=True
            smoke.append((character,pool.submit(req,a.out/('smoke-'+character),60)))
        for character,future in smoke:
            result,_=future.result()
            check('native_smoke_'+character,result['status'] in ('BUDGET','DECISION','TERMINAL') and len(result['trace'])>0,
                status=result['status'],actions=len(result['trace']),classification=classify_failure(result),reason=result.get('reason'))
        # Actually execute alternatives at a real map/card reward/shop/rest/event.
        ev=Evaluator(pool,ctx,a.out/'phase-comparisons',SearchConfig(evaluations=20,seconds=240,task_seconds=60,max_decisions=600,event_driven_settle=a.event_driven_settle))
        ev.identity=identity
        from spire_exact.planning.archive import CheckpointArchive
        ev.checkpoints=CheckpointArchive(ctx,identity);ev.checkpoints.import_result(base)
        groups=decision_groups(base,1000,set(),limit=2)
        for phase in ('map','card_reward','shop','rest','event'):
            group=next((g for g in groups if g['phase']==phase),None)
            if group is None:
                check('phase_'+phase,False,reason='not encountered in supplied prefix');continue
            specs=[{'kind':'native_phase_'+phase,'request':ev.request(prefix,horizon=group['floor']+1,
                actions=len(prefix)+32)} for prefix in group['prefixes']]
            results=ev.batch(specs)
            check('phase_'+phase,len(results)>=2 and all(len(r['trace'])>=len(s['request']['history']) for s,r,_ in results),
                outcomes=[{'classification':classify_failure(r),'quality':utility(r),'label':label} for _,r,label in results],
                semantic_coverage_complete=all(r['status']!='UNSUPPORTED' for _,r,_ in results))
        if a.planner_evaluations:
            planner_dir=a.out/'planner';planner_dir.mkdir()
            native=NativeCampaignBackend(ctx,planner_dir,pool.data,'combatsolver')
            native.advisor.update(budget_ms=250,boss_budget_ms=1000,dop=1,reuse_continuations=True)
            prefix=read_json(ROOT/'outputs/mode1-continuation-v5/candidate-001/request.json')['history']
            result=solve(ctx,planner_dir,pool,SearchConfig(evaluations=a.planner_evaluations,seconds=300,task_seconds=60,
                max_decisions=800,alternatives=3,survivors=1,lookahead_actions=64,lookahead_floors=1),
                advisor=native.advisor,initial_prefix=prefix,import_checkpoints=[Path(c['path']) for c in base['checkpoints']])
            check('native_P4_P5_execution',bool(result['lookahead_groups']),status=result['status'],
                best_observation=result.get('best_observation'),evaluations=len(result['evaluations']))
    report['successful']=all(c['passed'] for c in report['checks'])
    write_json(a.out/'report.json',report)
    print(json.dumps({'report':str(a.out/'report.json'),'successful':report['successful']}))
    return 0 if report['successful'] else 1
if __name__=='__main__':raise SystemExit(main())
