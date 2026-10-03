"""Exercise receding-horizon proposals and independently replay native states."""
from pathlib import Path
from concurrent.futures import as_completed
import os,sys,json
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.mode1 import context,NativeCampaignBackend
from spire_exact.planning.pool import NativePool
from spire_exact.planning.resources import ResourcePlan
from spire_exact.planning.io import write_json
from spire_exact.canonical import canonical
from tools.experiment_storage import prepare_storage

out=ROOT/'experiments/iteration-017/native-complete-regression';out.mkdir(parents=True,exist_ok=False);prepare_storage(out)
data=ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
os.environ['SPIRE_TARGET_CPUS']='8';os.environ['SPIRE_MEMORY_BUDGET_MIB']='6144'
if os.name=='nt':
    import ctypes
    k=ctypes.WinDLL('kernel32',use_last_error=True);k.GetCurrentProcess.restype=ctypes.c_void_p
    k.SetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
    if not k.SetProcessAffinityMask(k.GetCurrentProcess(),sum(1<<i for i in range(24,32))):raise ctypes.WinError(ctypes.get_last_error())
rows=[]
with NativePool(data,out/'workers',ResourcePlan.detect(2,1,1000,1536))as pool:
    pending={}
    for seed in ['0','1']:
        ctx=context(seed,'IRONCLAD',0,'all');adapter=NativeCampaignBackend(ctx,out/seed,data,'combatsolver')
        advisor=dict(adapter.advisor,budget_ms=400,boss_budget_ms=1600,profile='Low',dop=1,
                     reuse_continuations=True,complete_continuations_only=True)
        request={k:ctx[k]for k in ['seed','character','ascension','unlocks']}
        request.update(history=[],generate_candidate=True,advisor=advisor,max_decisions=300,capture_checkpoints=False)
        pending[pool.submit(request,out/seed/'candidate',90)]=(seed,ctx)
    for future in as_completed(pending):
        seed,ctx=pending[future]
        result,identity=future.result()
        replay_request={k:ctx[k]for k in ['seed','character','ascension','unlocks']}
        replay_request.update(history=result['trace'],generate_candidate=False,expected_evidence=result['decision_evidence'])
        replay,other=pool.run(replay_request,out/seed/'independent-replay',90,fresh=True)
        equal=canonical(result['observation'])==canonical(replay['observation']) and canonical(result['decision_evidence'])==canonical(replay['decision_evidence'])
        counters=result['advisor_metrics']['counters']
        rows.append({'seed':seed,'native_status':result['status'],'equal_observation_rng_and_evidence':equal,
                     'deferred_incomplete_forecasts':counters.get('turn_reuse_deferred_incomplete_forecast',0),
                     'reuse_matches':counters.get('continuation_state_match',0),'counts_as_validation_win':False})
        write_json(out/'results.json',rows);print(json.dumps(rows[-1]),flush=True)
passed=all(r['equal_observation_rng_and_evidence']for r in rows) and sum(r['deferred_incomplete_forecasts']for r in rows)>0
write_json(out/'report.json',{'passed':passed,'rows':rows,'scope':'original DLL offline TestMode; not normal Godot equivalence'})
raise SystemExit(0 if passed else 1)
