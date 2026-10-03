"""Cold/warm request reproduction under two process-local heap configurations."""
from pathlib import Path
from contextlib import ExitStack
import argparse,json,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.planning.io import read_json,write_json
from spire_exact.planning.pool import NativePool
from spire_exact.planning.resources import ResourcePlan
from spire_exact.canonical import canonical

PROFILES=('server-one-heap','server-bounded-heap')
GAME=('status','phase','reason','value','observation','trace','decision_evidence','native_terminal_observed')

def run(source,out):
    if out.exists()and any(out.iterdir()):raise ValueError('Fresh output required')
    out.mkdir(parents=True,exist_ok=True)
    request=read_json(source)['request'];request.pop('checkpoint',None)
    request.update(capture_checkpoints=False,low_io=True,event_driven_settle=True)
    data=ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
    plan=ResourcePlan.detect(1,1,1536,1024);rows=[]
    with ExitStack()as stack:
        pools={name:stack.enter_context(NativePool(data,out/(name+'-workers'),plan,runtime_profile=name))for name in PROFILES}
        for repeat in range(3):
            for name in(PROFILES if repeat%2==0 else PROFILES[::-1]):
                target=out/f'{repeat}-{name}';row={'repeat':repeat,'profile':name,'target':str(target),'valid':False}
                try:
                    result,_=pools[name].run(request,target,1200)
                    perf=result['performance'];searches=(result.get('advisor_metrics')or{}).get('searches',[])
                    row.update(status=result['status'],reason=result.get('reason'),performance=perf,
                               nodes=sum(s.get('expanded_nodes')or 0 for s in searches),
                               reported_time_boundary=any(s.get('time_boundary')for s in searches),
                               observed_heap_limit=perf['gc_configuration']['GCHeapHardLimit'])
                    row['valid']=(result['status']in('TERMINAL','BUDGET','DECISION')and
                                  canonical(result['trace'][:len(request['history'])])==canonical(request['history'])and
                                  not row['reported_time_boundary']and
                                  (name!='server-bounded-heap'or row['observed_heap_limit']==plan.worker_memory_bytes*3//4))
                    row['transport']=read_json(target/'transport.json')
                except Exception as error:
                    row['error']=str(error)
                    if(target/'failure.json').exists():row['failure']=read_json(target/'failure.json')
                rows.append(row);write_json(out/'partial-results.json',{'rows':rows})
                print(json.dumps({'event':'heap_case_completed','repeat':repeat,'profile':name,'valid':row['valid'],'error':row.get('error')}),flush=True)
    # Independent replays follow generation, so they cannot reset the warm workers.
    with NativePool(data,out/'replay-worker',plan,runtime_profile='server-one-heap')as pool:
        for row in rows:
            if not row['valid']:continue
            target=Path(row['target']);result=read_json(target/'data/decision.json')
            replay_request={k:request[k]for k in('seed','character','ascension','unlocks')}
            replay_request.update(history=result['trace'],expected_evidence=result['decision_evidence'],
                                  generate_candidate=False,capture_checkpoints=False,low_io=True,event_driven_settle=True)
            try:
                replay,replay_identity=pool.run(replay_request,out/('verify-'+target.name),600,fresh=True)
                keys=[k for k in GAME if k not in('status','phase','reason')]
                row['independent_replay']=(canonical({k:replay.get(k)for k in keys})==canonical({k:result.get(k)for k in keys})
                    and not replay.get('reason')and replay['performance']['pid']!=result['performance']['pid']
                    and canonical(replay_identity)==canonical(read_json(target/'data/identity.json'))
                    and replay['performance']['counters']['replay_prefix_solver_calls']==0
                    and replay['performance']['counters']['checkpoint_skipped_actions']==0)
            except Exception as error:row['replay_error']=str(error);row['independent_replay']=False
    pairs=[]
    for repeat in range(3):
        arms=[next(r for r in rows if r['repeat']==repeat and r['profile']==name)for name in PROFILES]
        if all(r['valid']for r in arms):
            results=[read_json(Path(r['target'])/'data/decision.json')for r in arms]
            pairs.append({'repeat':repeat,'both_valid':True,'game_equal':canonical({k:results[0].get(k)for k in GAME})==canonical({k:results[1].get(k)for k in GAME}),
                          'nodes_equal':arms[0]['nodes']==arms[1]['nodes']})
        else:pairs.append({'repeat':repeat,'both_valid':False})
    candidate_ok=all(r['valid']and r.get('independent_replay')for r in rows if r['profile']=='server-bounded-heap')
    control_comparable=all(p.get('game_equal',True)and p.get('nodes_equal',True)for p in pairs)
    replay_ok=all(r.get('independent_replay')for r in rows if r['valid'])
    report={'candidate_can_proceed':candidate_ok and control_comparable and replay_ok,'rows':rows,'pairs':pairs,
            'old_memory_failure_reproduced':any('MEMORY_BUDGET'in r.get('error','')for r in rows if r['profile']=='server-one-heap'),
            'scope':'three generations per persistent profile worker, sequential on P8; diagnostic only',
            'counts_as_fresh_win':False,'performance_promotion':False}
    write_json(out/'report.json',report);print(json.dumps({'event':'heap_probe_completed','candidate_can_proceed':report['candidate_can_proceed'],
         'old_memory_failure_reproduced':report['old_memory_failure_reproduced']}),flush=True)
    return report['candidate_can_proceed']

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    from tools import limited_cli
    sys.argv=[__file__,'--out',str(a.out)]
    def action(*args,**kwargs):
        if not run(a.source,a.out):raise SystemExit(1)
    limited_cli.runpy.run_module=action;limited_cli.main()
