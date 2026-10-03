"""Fixed-node, same-native-state CLR processor-count benchmark; not win evidence."""
from pathlib import Path
import sys,os,time,json,argparse
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.mode1 import NativeCampaignBackend
from spire_exact.native import export_native
from spire_exact.planning.io import read_json,write_json

p=argparse.ArgumentParser();p.add_argument('--out',type=Path,default=ROOT/'experiments/iteration-016/runtime-cpu-benchmark')
p.add_argument('--cpu-counts',type=int,nargs='+',default=[1,2]);p.add_argument('--repeats',type=int,default=2)
p.add_argument('--nodes',type=int,default=200);p.add_argument('--soft-ms',type=int,default=60000)
a=p.parse_args();out=a.out.resolve();out.mkdir(parents=True,exist_ok=False)
data=ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
source=ROOT/'experiments/frozen-i007/experiments/iteration-007/i007-fresh20-v2/seed-1497177934/eval-0042-probe_followup/data/decision.json'
native=read_json(source)
context=read_json(source.parents[2]/'result.json')['context']
entries={}
for i,e in enumerate(native['decision_evidence']):
    o=e.get('observation')or{}
    if e['phase']=='combat' and o.get('floor')in (1,17,33) and o['floor']not in entries:entries[o['floor']]=i
if os.name=='nt':
    import ctypes
    k=ctypes.WinDLL('kernel32',use_last_error=True);k.GetCurrentProcess.restype=ctypes.c_void_p
    k.SetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
    if not k.SetProcessAffinityMask(k.GetCurrentProcess(),sum(1<<i for i in range(24,32))):raise ctypes.WinError(ctypes.get_last_error())
backend=NativeCampaignBackend(context,out/'adapter',data,'combatsolver')
advisor=dict(backend.advisor,budget_ms=a.soft_ms,boss_budget_ms=a.soft_ms,nodes=a.nodes,profile='Low',dop=1,reuse_continuations=True)
rows=[]
write_json(out/'manifest.json',{'source':str(source),'context':context,'entries':entries,'solver_dop':1,
    'node_budget':a.nodes,'soft_ms':a.soft_ms,'cpu_affinity':list(range(24,32)),
    'comparison':'DOTNET_PROCESSOR_COUNT1 versus2; cold process per request; counterbalanced per repetition',
    'counts_as_fresh_validation':False})
for floor,index in sorted(entries.items()):
    for repeat in range(a.repeats):
        for cpus in (a.cpu_counts if repeat%2==0 else a.cpu_counts[::-1]):
            os.environ['DOTNET_PROCESSOR_COUNT']=str(cpus);os.environ['DOTNET_GCServer']='0'
            folder=out/f'floor-{floor}-repeat-{repeat}-cpus-{cpus}'
            request={k:context[k]for k in ['seed','character','ascension','unlocks']}
            start=time.perf_counter()
            try:
                export_native('replay',folder,data,timeout_seconds=90,**request,
                    history=native['trace'][:index],generate_candidate=True,max_decisions=index+1,
                    capture_checkpoints=False,advisor=advisor,compact=True)
                result=read_json(folder/'data/decision.json');metrics=result.get('advisor_metrics')or{}
                row={'floor':floor,'repeat':repeat,'runtime_cpus':cpus,'wall_seconds':time.perf_counter()-start,
                     'status':result['status'],'reason':result.get('reason'),'searches':metrics.get('searches'),
                     'performance':result.get('performance'),'action':result.get('trace',[None])[-1]}
            except Exception as error:row={'floor':floor,'repeat':repeat,'runtime_cpus':cpus,'error':str(error)}
            rows.append(row);write_json(out/'results.json',rows)
            print(json.dumps({k:v for k,v in row.items()if k!='performance'}),flush=True)
