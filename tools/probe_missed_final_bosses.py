"""Diagnostic only: previously generated losing prefixes, never fresh-win-rate evidence."""
from pathlib import Path
import json,os,sys,time
ROOT=Path(__file__).resolve().parents[1]
FROZEN=ROOT/'experiments/frozen-i007'
# Use the SAME frozen implementation as the failed original validation.
sys.path.insert(0,str(FROZEN))
from spire_exact.mode1 import NativeCampaignBackend
from spire_exact.planning.pool import NativePool
from spire_exact.planning.resources import ResourcePlan
from spire_exact.planning.archive import failure_combat_prefix,classify_failure
from spire_exact.planning.io import read_json,write_json

out=ROOT/'experiments/iteration-011/missed-boss-probes';out.mkdir(parents=True,exist_ok=False)
batch=FROZEN/'experiments/iteration-007/i007-fresh20-v2'
data=FROZEN/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
os.environ['SPIRE_TARGET_CPUS']='8';os.environ['SPIRE_MEMORY_BUDGET_MIB']='6144'
if os.name=='nt':
    import ctypes
    k=ctypes.WinDLL('kernel32',use_last_error=True);k.GetCurrentProcess.restype=ctypes.c_void_p
    k.SetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
    if not k.SetProcessAffinityMask(k.GetCurrentProcess(),sum(1<<i for i in range(8,16))):raise ctypes.WinError(ctypes.get_last_error())
pending=[];reports=[]
with NativePool(data,out/'workers',ResourcePlan.detect(4,1,1000,1536)) as pool:
    for row in read_json(batch/'results.json'):
        if row['win']:continue
        result_path=Path(row['result']);r=read_json(result_path)
        failures=[e for e in r['evaluations'] if e['classification']=='NATIVE_ROUTE_DEATH'
                  and (e.get('observation') or {}).get('floor')==48]
        if not failures:continue
        original=result_path.parent/failures[0]['label']/'data/decision.json'
        candidate=read_json(original);probe=failure_combat_prefix(candidate)
        if not probe:continue
        ctx=r['context'];folder=out/('seed-'+row['seed'])
        backend=NativeCampaignBackend(ctx,folder,data,'combatsolver')
        advisor=backend.advisor;advisor.update(budget_ms=1200,boss_budget_ms=4800,dop=1,profile='Low',reuse_continuations=True)
        request={k:ctx[k] for k in ['seed','character','ascension','unlocks']}
        request.update(history=probe['prefix'],generate_candidate=True,advisor=advisor,
            stop_at_floor=probe['floor'],max_decisions=len(probe['prefix'])+2400,capture_checkpoints=False)
        pending.append((row['seed'],folder,request,original,probe))
    for offset in range(0,len(pending),4):
        futures=[(item,pool.submit(item[2],item[1]/'probe',120)) for item in pending[offset:offset+4]]
        for (seed,folder,request,original,entry),future in futures:
            try:
                result,identity=future.result();classification=classify_failure(result)
                passed=classification in ('SEARCH_BUDGET','DECISION_BOUNDARY','NATIVE_WIN_CANDIDATE') and float(result['observation'].get('hp') or 0)>0
                record={'seed':seed,'source':str(original),'classification':classification,'boss_passed':passed,
                    'entry_hp':entry['entry_observation'].get('hp'),'enemy_ids':[e['id']for e in entry['entry_observation'].get('enemies',[])],
                    'final_hp':result.get('observation',{}).get('hp'),'performance':result.get('performance'),
                    'counts_as_fresh_validation_win':False,'independent_replay_checked':False}
                if passed:
                    follow={k:request[k] for k in ['seed','character','ascension','unlocks']}
                    follow.update(history=result['trace'],generate_candidate=True,max_decisions=2400)
                    final,identity=pool.run(follow,folder/'followup',60,fresh=True)
                    record['terminal_value']=final.get('value')
                    if final.get('status')=='TERMINAL' and final.get('value',[0])[0]==1:
                        replay={k:request[k] for k in ['seed','character','ascension','unlocks']}
                        replay.update(history=final['trace'],generate_candidate=False,expected_evidence=final['decision_evidence'])
                        checked,_=pool.run(replay,folder/'replay',90,fresh=True)
                        record['independent_replay_checked']=checked.get('reason') is None and checked.get('value',[0])[0]==1 and checked['decision_evidence']==final['decision_evidence']
                reports.append(record)
            except Exception as error:reports.append({'seed':seed,'error':str(error),'counts_as_fresh_validation_win':False})
            write_json(out/'report.json',reports);print(json.dumps({k:v for k,v in reports[-1].items() if k!='performance'}),flush=True)
