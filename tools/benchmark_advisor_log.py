"""Logging sink equivalence/byte diagnostic on a separate E-core partition."""
from pathlib import Path
import ctypes as c,json,os,sys,time,argparse
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.planning.io import read_json,write_json
from spire_exact.planning.pool import NativePool
from spire_exact.planning.resources import ResourcePlan
from spire_exact.canonical import canonical
from tools.cpu_topology import inventory,homogeneous_cpus
from tools.rolling_storage import logical_bytes

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--resume',action='store_true');args=parser.parse_args()
    out=ROOT/'experiments/iteration-027/diagnostic-log-pairs';out.mkdir(parents=True,exist_ok=args.resume)
    cpus,cls=homogeneous_cpus(inventory(),8,efficiency_class=0)
    k=c.WinDLL('kernel32',use_last_error=True);k.GetCurrentProcess.restype=c.c_void_p;k.SetProcessAffinityMask.argtypes=[c.c_void_p,c.c_size_t]
    if not k.SetProcessAffinityMask(k.GetCurrentProcess(),sum(1<<i for i in cpus)):raise c.WinError(c.get_last_error())
    os.environ.update(SPIRE_TARGET_CPUS='8',SPIRE_MEMORY_BUDGET_MIB='4096')
    source=ROOT/'experiments/frozen-i024/experiments/iteration-023/full-route-io-preflight/seed-564940356-repeat-0'
    records=read_json(source/'result.json')['evaluations'][::2][:12]
    data=ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
    plan=ResourcePlan.detect(1,1,1536,1024)
    rows=read_json(out/'report.json')['rows']if args.resume else []
    keys=['status','phase','reason','value','observation','trace','decision_evidence','native_terminal_observed']
    suffix=f'-resume-{int(time.time())}'if args.resume else ''
    with NativePool(data,out/('workers'+suffix),plan)as pool:
        if args.resume and read_json(out/'manifest.json')['identity']['host_sha256']!=pool.stamp['host_sha256']:
            raise ValueError('Cannot combine resumed cases across a changed host binary')
        write_json(out/('resume-manifest.json'if args.resume else'manifest.json'),{'scope':'native full-result logging equivalence; not fresh planner validation',
            'cpu_set':cpus,'efficiency_class':cls,'source':str(source),'cases':len(records),'identity':pool.stamp,
            'concurrent_load':'P-core frozen-i029 L2 diagnostic; no cross-run timing promotion',
            'memory_model_mib':4096,'worker_mib':1536,'no_installed_game_or_upstream_modification':True})
        for i,record in enumerate(records):
            if any(r['case']==i for r in rows):continue
            pair={}
            for quiet in ([False,True]if i%2==0 else[True,False]):
                req=read_json(source/record['label']/'request.json');req.pop('checkpoint',None)
                req.update(capture_checkpoints=False,low_io=True,event_driven_settle=True)
                req['advisor']=dict(req['advisor'],quiet_diagnostics=quiet)
                target=out/f'case-{i:02}{suffix}-quiet-{int(quiet)}'
                result,identity=pool.run(req,target,1200,fresh=True)
                # Await an orderly process exit so its asynchronous diagnostic
                # writer has flushed before measuring the retained bytes.
                for worker in pool.workers:worker.close()
                log=(result.get('advisor_metrics')or{}).get('diagnostic_log',{})
                nodes=sum(s.get('expanded_nodes',0)for s in (result.get('advisor_metrics')or{}).get('searches',[]))
                row={'case':i,'quiet':quiet,'status':result['status'],'nodes':nodes,'diagnostic_log':log,
                     'retained_bytes':logical_bytes(target),'process_log_bytes':sum(p.stat().st_size for p in target.rglob('process.jsonl')),
                     'wall_us':result['performance']['wall_us'],'cpu_us':result['performance']['cpu_us']}
                pair[quiet]=(canonical({key:result.get(key)for key in keys}),nodes,row)
            same=pair[False][:2]==pair[True][:2]
            rows.append({'case':i,'same_full_game_and_nodes':same,'arms':[pair[False][2],pair[True][2]]})
            write_json(out/'report.json',{'complete':False,'passed':False,'rows':rows})
            print(json.dumps({'event':'log_pair_completed','case':i,'same_full_game_and_nodes':same,
                              'normal_bytes':pair[False][2]['process_log_bytes'],'quiet_bytes':pair[True][2]['process_log_bytes']}),flush=True)
    passed=all(r['same_full_game_and_nodes']and r['arms'][1]['diagnostic_log'].get('enabled')is True for r in rows)
    write_json(out/'report.json',{'complete':True,'passed':passed,'rows':rows,'production_write_gate_measured':False})
    print(json.dumps({'event':'log_comparison_completed','passed':passed}),flush=True)
    raise SystemExit(0 if passed else 1)

if __name__=='__main__':main()
