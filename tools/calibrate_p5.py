"""Measure allowed worker×DOP combinations on the SAME native replay workload.

No heuristic candidate cache. Warmup excluded. Output is hardware-specific,
not a universal claim that more workers/DOP is faster.
"""
import argparse,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.planning.io import read_json,write_json
from spire_exact.planning.resources import ResourcePlan
from spire_exact.planning.pool import NativePool
from spire_exact.native import game_data
from spire_exact.canonical import canonical

def main():
    p=argparse.ArgumentParser();p.add_argument('--game-dir',type=Path,required=True)
    p.add_argument('--request',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--jobs',type=int,default=6);p.add_argument('--worker-memory-mib',type=int,default=900)
    a=p.parse_args()
    if a.out.exists() and any(a.out.iterdir()):p.error('output must be empty')
    if a.jobs<2:p.error('jobs must be >=2')
    a.out.mkdir(parents=True,exist_ok=True)
    request=read_json(a.request);request.pop('out',None);request.pop('advisor',None)
    request.update(generate_candidate=False,capture_checkpoints=False,compact=True)
    data=game_data(a.game_dir);results=[];seen=set();expected=None
    for workers,dop in [(1,1),(2,1),(1,2),(2,2),(4,1)]:
        try:plan=ResourcePlan.detect(workers,dop,a.worker_memory_mib)
        except MemoryError:continue
        key=(plan.workers,plan.dop)
        if key in seen:continue
        seen.add(key);base=a.out/f'w{key[0]}-d{key[1]}'
        with NativePool(data,base/'workers',plan) as pool:
            warm=[pool.submit(request,base/f'warm-{i}',90) for i in range(plan.workers)]
            for f in warm:f.result()
            start=time.perf_counter()
            jobs=[pool.submit(request,base/f'timed-{i}',90) for i in range(a.jobs)]
            rows=[f.result()[0] for f in jobs]
            seconds=time.perf_counter()-start
            same=True
            for row in rows:
                state=canonical({'status':row['status'],'trace':row['trace'],
                    'decision_evidence':row['decision_evidence'],'observation':row['observation']})
                if expected is None:expected=state
                same &= state==expected
            results.append({'resources':plan.as_dict(),'jobs':a.jobs,'seconds':seconds,
                'jobs_per_second':a.jobs/seconds,'same_semantic_results':same,
                'pids':sorted({r['performance']['pid'] for r in rows}),
                'peak_rss_bytes':max(r['performance']['peak_working_set_bytes'] for r in rows)})
    valid=[x for x in results if x['same_semantic_results']]
    best=max(valid,key=lambda x:x['jobs_per_second']) if valid else None
    write_json(a.out/'calibration.json',{'scope':'warm fixed native replay, not beam quality or whole-run speed',
        'results':results,'recommended_for_this_workload':best})
    print(a.out/'calibration.json')
if __name__=='__main__':main()
