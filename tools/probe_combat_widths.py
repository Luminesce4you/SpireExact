"""Same native boss entry, two combat widths; diagnostic prefixes, not fresh runs."""
from pathlib import Path
import sys,os,json,argparse
from concurrent.futures import as_completed
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.mode1 import NativeCampaignBackend,check_winning_replay
from spire_exact.planning.pool import NativePool
from spire_exact.planning.resources import ResourcePlan
from spire_exact.planning.archive import classify_failure,failure_combat_prefix
from spire_exact.planning.io import read_json,write_json

p=argparse.ArgumentParser()
p.add_argument('--out',type=Path,default=ROOT/'experiments/iteration-012/boss-width-probes')
p.add_argument('--widths',type=int,nargs='+',default=[45,12])
p.add_argument('--boss-ms',type=int,default=4800);p.add_argument('--timeout',type=int,default=90)
p.add_argument('--offset',type=int,default=8);p.add_argument('--max-cases',type=int)
a=p.parse_args();out=a.out.resolve();out.mkdir(parents=True,exist_ok=False)
base=ROOT/'experiments/frozen-i007/experiments/iteration-007/i007-fresh20-v2'
data=ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
os.environ['SPIRE_TARGET_CPUS']='8';os.environ['SPIRE_MEMORY_BUDGET_MIB']='6144'
if os.name=='nt':
    import ctypes
    k=ctypes.WinDLL('kernel32',use_last_error=True);k.GetCurrentProcess.restype=ctypes.c_void_p
    k.SetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
    if not k.SetProcessAffinityMask(k.GetCurrentProcess(),sum(1<<i for i in range(a.offset,a.offset+8))):raise ctypes.WinError(ctypes.get_last_error())
jobs=[];reports=[];cases=0
for row in read_json(base/'results.json'):
    if row['win']:continue
    result_path=Path(row['result']);r=read_json(result_path)
    ev=next((e for e in r['evaluations'] if e['classification']=='NATIVE_ROUTE_DEATH'
             and (e.get('observation')or{}).get('floor')==48),None)
    if ev is None:continue
    source=result_path.parent/ev['label']/'data/decision.json';probe=failure_combat_prefix(read_json(source))
    if probe is None:continue
    if a.max_cases is not None and cases>=a.max_cases:break
    cases+=1
    ctx=r['context'];backend=NativeCampaignBackend(ctx,out/('seed-'+row['seed']),data,'combatsolver')
    for width in a.widths:
        advisor=dict(backend.advisor,budget_ms=1200,boss_budget_ms=a.boss_ms,profile='Low',beam=width,dop=1,reuse_continuations=True)
        request={k:ctx[k] for k in ['seed','character','ascension','unlocks']}
        request.update(history=probe['prefix'],generate_candidate=True,advisor=advisor,max_decisions=2400,capture_checkpoints=False)
        jobs.append({'seed':row['seed'],'width':width,'source':str(source),'request':request,'context':ctx})
write_json(out/'manifest.json',{'counts_as_fresh_validation':False,'paired_requests':jobs})
with NativePool(data,out/'workers',ResourcePlan.detect(4,1,1000,1536)) as pool:
    for start in range(0,len(jobs),4):
        pending={pool.submit(j['request'],out/f"seed-{j['seed']}-beam{j['width']}",a.timeout):j for j in jobs[start:start+4]}
        for future in as_completed(pending):
            j=pending[future];row={k:j[k]for k in ['seed','width','source']}
            try:
                result,identity=future.result();row.update(classification=classify_failure(result),independent_replay_checked=False,
                    search_records=(result.get('advisor_metrics')or{}).get('searches'),performance=result.get('performance'))
                if row['classification']=='NATIVE_WIN_CANDIDATE':
                    req={k:j['context'][k]for k in ['seed','character','ascension','unlocks']}
                    req.update(history=result['trace'],generate_candidate=False,expected_evidence=result['decision_evidence'])
                    replay,other=pool.run(req,out/f"replay-{j['seed']}-{j['width']}",90,fresh=True)
                    cert=check_winning_replay(result,replay,{'context':j['context'],'native':identity},{'context':j['context'],'native':other})
                    write_json(out/f"certificate-{j['seed']}-{j['width']}.json",cert);row['independent_replay_checked']=True
            except Exception as error:row.update(classification='DIAGNOSTIC_ERROR',error=str(error))
            reports.append(row);write_json(out/'report.json',reports);print(json.dumps({k:v for k,v in row.items()if k not in ('search_records','performance')}),flush=True)
