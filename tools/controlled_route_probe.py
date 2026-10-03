"""Matched continuation controls for the first L2 route rescue.

Both arms force an equally long prefix and restart policy RNG identically.
Uses the already validated frozen-i029 host, not unvalidated logging changes.
"""
from pathlib import Path
import ctypes as c,json,os,subprocess,sys
ROOT=Path(__file__).resolve().parents[1]
FROZEN=ROOT/'experiments/frozen-i029';sys.path.insert(0,str(FROZEN))
from spire_exact.planning.io import read_json,write_json
from spire_exact.planning.pool import NativePool
from spire_exact.planning.resources import ResourcePlan
from spire_exact.canonical import canonical
from tools.cpu_topology import inventory,homogeneous_cpus

OUT=ROOT/'experiments/iteration-026/matched-route-control'
SOURCE=FROZEN/'experiments/iteration-026/l2-preparation-baseline/cases/act-2-target-33-root-02-repeat-0'

def run():
    OUT.mkdir(parents=True,exist_ok=True)
    parent=read_json(SOURCE/'eval-0000-baseline/data/decision.json')
    request=read_json(SOURCE/'eval-0003-macro_route/request.json');request.pop('checkpoint',None)
    request.update(capture_checkpoints=False)
    index=len(request['history'])-1
    assert canonical(parent['trace'][:index])==canonical(request['history'][:index])
    expected=parent['decision_evidence'][index]['observation']
    requests={'old-choice':dict(request,history=parent['trace'][:index+1]),'new-choice':request}
    without_history=[canonical({k:v for k,v in req.items()if k not in ('history','out')})for req in requests.values()]
    if without_history[0]!=without_history[1]:raise ValueError('Confounded request settings')
    write_json(OUT/'manifest.json',{'source':str(SOURCE),'scope':'matched continuation RNG reset; same prior native state, one forced route action',
              'old_action':requests['old-choice']['history'][-1],'new_action':requests['new-choice']['history'][-1],
              'prefix_length':index+1,'policy_seed':request['policy_seed'],'counts_as_fresh_planner_win':False,
              'concurrent_load':'P-core L2 run active; E-core component outcomes only, no timing promotion'})
    rows=[]
    with NativePool(FROZEN/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64',OUT/'workers',ResourcePlan.detect(2,1,1536,1024))as pool:
        futures={name:pool.submit(req,OUT/name,1200,fresh=True)for name,req in requests.items()}
        for name,future in futures.items():
            result,identity=future.result()
            same_entry=canonical(result['decision_evidence'][index]['observation'])==canonical(expected)
            if not same_entry:raise ValueError('Native pre-choice entry diverged')
            obs=result['observation'];alive=float(obs.get('hp')or 0)>0
            rescued=alive and result.get('reason')=='candidate_horizon'and obs.get('floor')==33
            verified=False
            if rescued:
                replay_request={k:request[k]for k in ('seed','character','ascension','unlocks')}
                replay_request.update(history=result['trace'],generate_candidate=False,capture_checkpoints=False,
                                      expected_evidence=result['decision_evidence'],low_io=True,event_driven_settle=True)
                replay,other=pool.run(replay_request,OUT/('verify-'+name),600,fresh=True)
                verified=(canonical(replay['trace'])==canonical(result['trace'])and canonical(replay['decision_evidence'])==canonical(result['decision_evidence'])
                          and canonical(replay['observation'])==canonical(obs)and canonical(other)==canonical(identity))
                if not verified:raise ValueError('Independent control replay diverged')
            row={'arm':name,'status':result['status'],'reason':result.get('reason'),'floor':obs.get('floor'),'hp':obs.get('hp'),
                 'same_native_pre_choice_entry':same_entry,'rescued':rescued,'independent_replay_verified':verified,
                 'expanded_nodes':sum(s.get('expanded_nodes',0)for s in (result.get('advisor_metrics')or{}).get('searches',[])),
                 'terminal_combat':result.get('terminal_combat')}
            rows.append(row);write_json(OUT/'partial.json',rows);print(json.dumps(row),flush=True)
    write_json(OUT/'report.json',{'complete':True,'rows':rows,'not_a_generalization_estimate':True})

if __name__=='__main__':
    if '--child'in sys.argv:
        from tools import limited_cli
        sys.argv=[__file__,'--out',str(OUT/'job')]
        limited_cli.runpy.run_module=lambda *a,**k:run()
        limited_cli.main()
    else:
        cpus,_=homogeneous_cpus(inventory(),8,efficiency_class=0)
        k=c.WinDLL('kernel32',use_last_error=True);k.GetCurrentProcess.restype=c.c_void_p;k.SetProcessAffinityMask.argtypes=[c.c_void_p,c.c_size_t]
        if not k.SetProcessAffinityMask(k.GetCurrentProcess(),sum(1<<i for i in cpus)):raise c.WinError(c.get_last_error())
        env=dict(os.environ,SPIRE_PROTOCOL='A10-seed-v2',SPIRE_WALL_LIMIT_SECONDS='1800',PYTHONIOENCODING='utf-8')
        raise SystemExit(subprocess.call([sys.executable,__file__,'--child'],cwd=ROOT,env=env))
