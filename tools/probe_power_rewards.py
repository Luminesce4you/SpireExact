"""Matched native counterfactuals on diagnostic seeds; not validation evidence.

Select actual Power offers by native catalog type and trajectory order, never by
card name or outcome. Each pair shares exactly the same preceding action history.
No card is injected; both branches execute an action in the recorded native menu.
"""
from pathlib import Path
import json,os,sys
from concurrent.futures import as_completed
ROOT=Path(__file__).resolve().parents[1]
FROZEN=ROOT/'experiments/frozen-i007'
sys.path.insert(0,str(FROZEN))
from spire_exact.mode1 import NativeCampaignBackend,check_winning_replay
from spire_exact.planning.pool import NativePool
from spire_exact.planning.resources import ResourcePlan
from spire_exact.planning.archive import classify_failure
from spire_exact.planning.io import read_json,write_json

def main():
    out=ROOT/'experiments/iteration-011/power-counterfactuals'
    out.mkdir(parents=True,exist_ok=False)
    batch=FROZEN/'experiments/iteration-007/i007-fresh20-v2'
    data=FROZEN/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
    catalog=read_json(ROOT/'experiments/iteration-011/native-catalog/data/catalog.json')
    types={c['id']:c['type'] for c in catalog['cards']}
    os.environ['SPIRE_TARGET_CPUS']='8';os.environ['SPIRE_MEMORY_BUDGET_MIB']='6144'
    if os.name=='nt':
        import ctypes
        k=ctypes.WinDLL('kernel32',use_last_error=True);k.GetCurrentProcess.restype=ctypes.c_void_p
        k.SetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
        if not k.SetProcessAffinityMask(k.GetCurrentProcess(),sum(1<<i for i in range(24,32))):raise ctypes.WinError(ctypes.get_last_error())
    specs=[];reports=[]
    for row in read_json(batch/'results.json'):
        if row['win']:continue
        path=Path(row['result']);result=read_json(path)
        deaths=[e for e in result['evaluations'] if e['classification']=='NATIVE_ROUTE_DEATH'
                and (e.get('observation')or{}).get('floor')==48]
        if not deaths:continue
        source=path.parent/deaths[0]['label']/'data/decision.json'
        candidate=read_json(source);offers=[];acts=set()
        for index,(action,evidence) in enumerate(zip(candidate['trace'],candidate['decision_evidence'])):
            if evidence['phase']!='card_reward':continue
            obs=evidence['observation'];act=obs['act']
            if act in acts:continue
            alternatives=[a for a in evidence['available_actions'] if a.get('kind')=='card_reward'
                          and types.get(a.get('card'))=='Power' and a!=action]
            if not alternatives:continue
            # First actual unchosen power offer in each act; no outcome-based picking.
            offers.append((index,action,alternatives[0],obs));acts.add(act)
        ctx=result['context'];backend=NativeCampaignBackend(ctx,out/('seed-'+row['seed']),data,'combatsolver')
        advisor=backend.advisor;advisor.update(budget_ms=400,boss_budget_ms=1600,dop=1,profile='Low',reuse_continuations=True)
        for index,original,alternative,obs in offers:
            for arm,action in [('original',original),('power',alternative)]:
                request={k:ctx[k] for k in ['seed','character','ascension','unlocks']}
                request.update(history=candidate['trace'][:index]+[action],generate_candidate=True,
                               advisor=advisor,policy_seed=0,max_decisions=2400,capture_checkpoints=False)
                specs.append({'seed':row['seed'],'index':index,'floor':obs['floor'],'arm':arm,
                              'action':action,'source':str(source),'request':request,'context':ctx})
    write_json(out/'manifest.json',{'source_workspace':str(FROZEN),'paired_arms':len(specs),
                'selection':'first unchosen native Power reward per act on first final-boss death',
                'counts_as_fresh_validation':False,'specs':specs})
    with NativePool(data,out/'workers',ResourcePlan.detect(4,1,1000,1536)) as pool:
        # Keep queue bounded so per-worker deadlines start near actual execution.
        for start in range(0,len(specs),4):
            pending={pool.submit(s['request'],out/f"seed-{s['seed']}-choice-{s['index']}-{s['arm']}",90):s
                     for s in specs[start:start+4]}
            for future in as_completed(pending):
                spec=pending[future];record={k:v for k,v in spec.items() if k not in ('request','context')}
                try:
                    result,identity=future.result();obs=result.get('observation')or{}
                    record.update(classification=classify_failure(result),floor_reached=obs.get('floor'),
                                  act_reached=obs.get('act'),hp=obs.get('hp'),independent_replay_checked=False)
                    if record['classification']=='NATIVE_WIN_CANDIDATE':
                        request={k:spec['context'][k] for k in ['seed','character','ascension','unlocks']}
                        request.update(history=result['trace'],generate_candidate=False,expected_evidence=result['decision_evidence'])
                        replay,replay_identity=pool.run(request,out/f"replay-{spec['seed']}-{spec['index']}-{spec['arm']}",90,fresh=True)
                        certificate=check_winning_replay(result,replay,{'context':spec['context'],'native':identity},
                                                        {'context':spec['context'],'native':replay_identity})
                        record['independent_replay_checked']=True
                        write_json(out/f"certificate-{spec['seed']}-{spec['index']}-{spec['arm']}.json",certificate)
                except Exception as error:record.update(error=str(error),classification='DIAGNOSTIC_ERROR')
                reports.append(record);write_json(out/'report.json',reports);print(json.dumps(record),flush=True)

if __name__=='__main__':main()
