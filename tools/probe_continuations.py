"""Separate unavailable forecasts from actual native continuation mismatches."""
from pathlib import Path
import os,sys,json
from collections import Counter
from concurrent.futures import as_completed
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.mode1 import NativeCampaignBackend,context
from spire_exact.planning.pool import NativePool
from spire_exact.planning.resources import ResourcePlan
from spire_exact.planning.archive import classify_failure
from spire_exact.planning.io import read_json,write_json

out=ROOT/'experiments/iteration-014/continuation-probes';out.mkdir(parents=True,exist_ok=False)
data=ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
os.environ['SPIRE_TARGET_CPUS']='8';os.environ['SPIRE_MEMORY_BUDGET_MIB']='6144'
if os.name=='nt':
    import ctypes
    k=ctypes.WinDLL('kernel32',use_last_error=True);k.GetCurrentProcess.restype=ctypes.c_void_p
    k.SetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
    if not k.SetProcessAffinityMask(k.GetCurrentProcess(),sum(1<<i for i in range(16,24))):raise ctypes.WinError(ctypes.get_last_error())
cases=[('fresh-'+seed,context(seed,'IRONCLAD',0,'all'),[],None)for seed in ['0','1']]
base=ROOT/'experiments/frozen-i007/experiments/iteration-007/i007-fresh20-v2'
for row in [r for r in read_json(base/'results.json')if r['win']][:2]:
    path=Path(row['result']);result=read_json(path)
    won=next(e for e in result['evaluations']if e['classification']=='NATIVE_WIN_CANDIDATE')
    source=path.parent/won['label']/'data/decision.json';native=read_json(source)
    index=next(i for i,e in enumerate(native['decision_evidence'])if e['phase']=='combat' and e['observation']['floor']==48)
    cases.append(('boss-'+row['seed'],result['context'],native['trace'][:index],str(source)))
reports=[]
with NativePool(data,out/'workers',ResourcePlan.detect(4,1,1000,1536)) as pool:
    pending={}
    for label,ctx,prefix,source in cases:
        backend=NativeCampaignBackend(ctx,out/label,data,'combatsolver')
        advisor=dict(backend.advisor,budget_ms=400,boss_budget_ms=1600,profile='Low',dop=1,reuse_continuations=True)
        request={k:ctx[k]for k in ['seed','character','ascension','unlocks']}
        request.update(history=prefix,generate_candidate=True,advisor=advisor,max_decisions=2400,capture_checkpoints=False)
        pending[pool.submit(request,out/label/'native',90)]=(label,source)
    for future in as_completed(pending):
        label,source=pending[future]
        try:
            result,identity=future.result();metrics=result.get('advisor_metrics')or{}
            searches=metrics.get('searches')or[]
            row={'label':label,'source':source,'classification':classify_failure(result),'counts_as_validated_win':False,
                 'counters':metrics.get('counters'),'differences':metrics.get('continuation_differences'),
                 'searched_turns':dict(Counter(str(s.get('searched_turns'))for s in searches)),
                 'boundaries':dict(Counter(s.get('boundary')for s in searches))}
        except Exception as error:row={'label':label,'error':str(error)}
        reports.append(row);write_json(out/'report.json',reports)
        print(json.dumps({k:v for k,v in row.items()if k!='differences'}),flush=True)
