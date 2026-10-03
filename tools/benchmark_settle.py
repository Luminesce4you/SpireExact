"""Paired full native replays for a request-scoped event-driven settle trial.

Stored diagnostic traces are never planner answers. Both arms use fresh owned
processes, identical original game DLLs, and compare full evidence bytes.
"""
from pathlib import Path
import argparse,ctypes as c,json,os,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.planning.io import read_json,write_json
from spire_exact.planning.pool import NativePool
from spire_exact.planning.resources import ResourcePlan
from spire_exact.native import build_host
from spire_exact.canonical import canonical
from tools.cpu_topology import inventory

def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True)
    p.add_argument('--cases',type=int,default=12);a=p.parse_args()
    out=a.out.resolve();out.mkdir(parents=True,exist_ok=False)
    # Separate E cores from the running P-core baseline. This is a diagnostic
    # paired comparison, not a substitute for the frozen P-core M0 benchmark.
    rows=inventory();cls=min(r['efficiency_class'] for r in rows)
    cpus=[r['logical_cpu'] for r in rows if r['efficiency_class']==cls][:4]
    k=c.WinDLL('kernel32',use_last_error=True);k.GetCurrentProcess.restype=c.c_void_p
    k.SetProcessAffinityMask.argtypes=[c.c_void_p,c.c_size_t]
    if not k.SetProcessAffinityMask(k.GetCurrentProcess(),sum(1<<i for i in cpus)):raise c.WinError(c.get_last_error())
    os.environ['SPIRE_TARGET_CPUS']='4';os.environ['SPIRE_MEMORY_BUDGET_MIB']='2048'
    source=ROOT/'experiments/frozen-i021/experiments/iteration-021/m0-long-dev00-s271828/seed-564940356'
    records=read_json(source/'result.json')['evaluations']
    indices=sorted({i*(len(records)-1)//max(1,a.cases-1) for i in range(a.cases)})
    data=ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
    _,_,build=build_host(data);write_json(out/'native-build.json',build)
    report={'passed':True,'cases':[],'cpu_set':cpus,'efficiency_class':cls,'planner_wins':0,
            'scope':'stored full native replay diagnostic; simultaneous frozen P-core baseline; normal Godot parity unverified'}
    with NativePool(data,out/'workers',ResourcePlan.detect(1,1,768,1024))as pool:
        for i,index in enumerate(indices):
            label=records[index]['label'];old=read_json(source/label/'data/decision.json')
            req0=read_json(source/label/'request.json')
            request={k:req0[k]for k in ['seed','character','ascension','unlocks']}
            request.update(history=old['trace'],generate_candidate=False,low_io=True)
            arms={};semantic={}
            # Alternate arm order to reduce warm-machine/order bias.
            for enabled in ([False,True]if i%2==0 else[True,False]):
                name='event'if enabled else'fixed';target=out/f'case-{index:04d}-{name}'
                result,identity=pool.run({**request,'event_driven_settle':enabled},target,180,fresh=True)
                semantic[name]=canonical({k:result.get(k)for k in ['status','phase','reason','value','observation','trace','decision_evidence','native_terminal_observed']})
                perf=result['performance'];stage=perf.get('exclusive_stages',{}).get('settle',{})
                arms[name]={'status':result['status'],'reason':result.get('reason'),'wall_us':perf['wall_us'],
                            'cpu_us':perf['cpu_us'],'settle_us':stage.get('us',0),'settle_calls':stage.get('calls',0),
                            'counters':perf['counters']}
            same=semantic['event']==semantic['fixed']
            complete=all(v['status']in('TERMINAL','DECISION')and not v['reason']for v in arms.values())
            row={'index':index,'source':label,'actions':len(old['trace']),'evidence_equal':same,'complete_prefix':complete,'arms':arms}
            report['cases'].append(row);report['passed'] &=same and complete
            write_json(out/'report.json',report)
            print(json.dumps({'event':'pair_completed',**row}),flush=True)
        report['identity']=identity
    for name in ['fixed','event']:
        report[name]={key:sum(r['arms'][name][key]for r in report['cases'])for key in ['wall_us','cpu_us','settle_us','settle_calls']}
    report['wall_speedup']=report['fixed']['wall_us']/max(1,report['event']['wall_us'])
    write_json(out/'report.json',report)
    print(json.dumps({'event':'settle_benchmark_completed','passed':report['passed'],'speedup':report['wall_speedup']}),flush=True)
    raise SystemExit(0 if report['passed']else 1)

if __name__=='__main__':main()
