"""Forensics of retained matched campaign tasks, not a hardware counter profiler."""
from collections import Counter,defaultdict
import argparse
import ctypes
import json
import math
import os
from pathlib import Path
import statistics
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.analyze_smt_seed import arm,load
from spire_exact.planning.io import write_json


def details(folder):
    summary,rows,seed=arm(folder.resolve())
    all_rows=[json.loads(line) for line in (seed/'evaluations.jsonl').read_text().splitlines()]
    epoch=(seed/'evaluations.jsonl').stat().st_mtime-all_rows[-1]['completed_wall_seconds']
    stats=Counter();stages=Counter();records=[];intervals=[]
    perpid=defaultdict(list)
    horizon=summary['observed_wall_seconds']
    for row in all_rows:
        path=seed/row['label']/'transport.json'
        if not path.exists():continue
        tx=load(path);end=path.stat().st_mtime-epoch;start=end-tx['wall_seconds']
        left,right=max(0.,start),min(horizon,end)
        if left<right:intervals.append((left,right))
    for row in rows:
        perf=row.get('performance') or {};searches=(row.get('advisor_metrics') or {}).get('searches',[])
        path=seed/row['label']/'transport.json'
        tx=load(path) if path.exists() else {}
        search_wall=sum(s.get('wall_us',0) for s in searches)/1e6
        pauses=sum(s.get('gc_pause_ms',0) for s in searches)/1000
        node=row.get('expanded_combat_nodes',0)
        stats['cycles']+=perf.get('cycles',0);stats['allocated_bytes']+=perf.get('allocated_bytes',0)
        stats['search_allocated_bytes']+=sum(s.get('allocated_bytes',0) for s in searches)
        stats['worker_yields']+=sum(s.get('worker_yields',0) for s in searches)
        stats['frame_waits']+=sum(s.get('frame_waits',0) for s in searches)
        for generation,n in enumerate(perf.get('collections',[])):stats[f'gen{generation}']+=n
        for name,value in perf.get('exclusive_stages',{}).items():stages[name]+=value['us']/1e6
        end=path.stat().st_mtime-epoch if path.exists() else None
        item={'label':row['label'],'nodes':node,'search_seconds':search_wall,'gc_pause_seconds':pauses,
              'cycles':perf.get('cycles'),'allocated_bytes':perf.get('allocated_bytes'),'completed_wall_seconds':row['completed_wall_seconds'],
              'physical_done_estimate_seconds':end,'absorption_wait_estimate_seconds':row['completed_wall_seconds']-end if end is not None else None,
              'pid':tx.get('pid'),'worker_job':tx.get('worker_job'),'queue_seconds':tx.get('queue_seconds'),
              'transport_seconds':tx.get('wall_seconds'),'native_seconds':perf.get('wall_us',0)/1e6,
              'gen0':(perf.get('collections') or [0,0,0])[0],'gen1':(perf.get('collections') or [0,0,0])[1],
              'gen2':(perf.get('collections') or [0,0,0])[2]}
        records.append(item);perpid[tx.get('pid')].append(item)
    events=[]
    for start,end in intervals:events.extend([(start,1),(end,-1)])
    events.sort();active=0;last=0;times=Counter()
    for at,delta in events:
        times[active]+=at-last;last=at;active+=delta
    times[active]+=horizon-last
    busy=sum(n*seconds for n,seconds in times.items())
    waits=[r['absorption_wait_estimate_seconds'] for r in records if r['absorption_wait_estimate_seconds'] is not None]
    summary.update(task_stage_seconds=dict(stages),cost_counts=dict(stats),
        cycles_per_node=stats['cycles']/summary['combat_nodes'],allocated_bytes_per_node=stats['search_allocated_bytes']/summary['combat_nodes'],
        non_stw_search_seconds=summary['search_seconds']-summary['gc_pause_seconds'],
        observed_completed_task_occupancy_lower_bound=busy/(summary['resources']['workers']*horizon),
        completed_task_concurrency_lower_bound_seconds=dict(times),
        absorption_wait_median_estimate=statistics.median(waits),absorption_wait_max_estimate=max(waits),
        process_first_job_evaluations=sum(r['worker_job']==1 for r in records),distinct_worker_processes=len(perpid),
        time_epoch_estimate=epoch,
        timeline_scope='Transport file mtime and ledger final mtime; service excludes process start. Unfinished/cancelled tasks missing: completed-task occupancy is a lower bound, not full utilization.')
    return summary,records


def analyze(folders):
    values=[details(Path(p)) for p in folders]
    base={r['label']:r for r in values[0][1]};comparisons=[]
    for summary,rows in values[1:]:
        pairs=[]
        for r in rows:
            if r['label'] not in base:continue
            a=base[r['label']]
            if a['nodes']!=r['nodes']:continue
            pairs.append({'label':r['label'],'nodes':r['nodes'],
                'search_ratio':r['search_seconds']/a['search_seconds'] if a['search_seconds'] else None,
                'non_stw_ratio':(r['search_seconds']-r['gc_pause_seconds'])/(a['search_seconds']-a['gc_pause_seconds']) if a['search_seconds']>a['gc_pause_seconds'] else None,
                'cycles_ratio':r['cycles']/a['cycles'] if a.get('cycles') else None,
                'left_worker_job':a['worker_job'],'right_worker_job':r['worker_job'],
                'left_gc_generations':[a['gen0'],a['gen1'],a['gen2']],'right_gc_generations':[r['gen0'],r['gen1'],r['gen2']],
                'left_search':a['search_seconds'],'right_search':r['search_seconds']})
        comparisons.append({'run':summary['run'],'pairs':pairs,'median_task_search_ratio':statistics.median(p['search_ratio'] for p in pairs if p['search_ratio'] is not None),
            'median_task_cycles_ratio':statistics.median(p['cycles_ratio'] for p in pairs if p['cycles_ratio'] is not None),
            'median_warm_task_search_ratio':statistics.median(p['search_ratio'] for p in pairs if p['search_ratio'] is not None and p['left_worker_job']>=3 and p['right_worker_job']>=3)})
    return {'schema':'spire-smt-cost-forensics/v1','arms':[v[0] for v in values], 'task_rows':[v[1] for v in values],'comparisons':comparisons,
        'cycles_caution':'Process-wide cycles include runtime/GC. Frequency and executed instructions are not measured. Cycles/node is not IPC or utilization.',
        'stw_caution':'Subtracting reported pauses does not remove background GC, barriers or extra GC allocation work.'}


def main():
    if os.name=='nt':ctypes.windll.kernel32.SetProcessAffinityMask(ctypes.windll.kernel32.GetCurrentProcess(),ctypes.c_size_t((1<<28)|(1<<29)))
    p=argparse.ArgumentParser();p.add_argument('runs',nargs='+',type=Path);p.add_argument('--out',required=True,type=Path);a=p.parse_args()
    result=analyze(a.runs);write_json(a.out,result)
    for row in result['arms']:
        print(json.dumps({k:row[k] for k in ('run','cycles_per_node','allocated_bytes_per_node','cost_counts','task_stage_seconds','observed_completed_task_occupancy_lower_bound','absorption_wait_median_estimate','absorption_wait_max_estimate','distinct_worker_processes')},ensure_ascii=False))
    for row in result['comparisons']:print(json.dumps({k:v for k,v in row.items() if k!='pairs'},ensure_ascii=False))


if __name__=='__main__':main()
