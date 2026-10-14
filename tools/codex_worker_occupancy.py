"""Read-only task-wall/phase accounting, bound to E cores; no instrumentation change."""
import argparse
from collections import Counter
import json
from pathlib import Path
import statistics
from codex_checkpoint_freeze import ART, affinity

def read(p): return json.loads(p.read_text(encoding='utf-8-sig'))

def main():
    p=argparse.ArgumentParser(); p.add_argument('--out',type=Path,required=True); a=p.parse_args(); affinity()
    if a.out.exists(): raise SystemExit('Use a fresh report path')
    rows=[]
    for run in sorted((ART/'iteration-105-ablation').iterdir()):
        if not run.is_dir(): continue
        seed_dirs=list(run.glob('seed-*')); seeds=[s for s in seed_dirs if (s/'evaluations.jsonl').is_file()]
        if not seeds: continue
        seed=seeds[0]; resource_path=run/(seed.name+'-resources.json')
        if not resource_path.exists(): continue
        resources=read(resource_path); wall=resources['wall_seconds']
        phases=Counter(); native=0.; occupied=0.; transport_count=0; measured=0; cached=0; failures=0
        # Use all recorded evaluation transports, including completed tasks
        # not absorbed before shutdown. Verification is a separate service.
        for t in seed.glob('eval-*/transport.json'):
            v=read(t); occupied+=v['wall_seconds']; transport_count+=1
        ledger_workers=None
        for line in (seed/'evaluations.jsonl').open(encoding='utf-8-sig'):
            try: ev=json.loads(line)
            except ValueError: continue
            if ev.get('cache_hit'): cached+=1; continue
            perf=ev.get('performance') or {}
            if not perf: failures+=1; continue
            measured+=1; native+=(perf.get('wall_us') or 0)/1e6
            for stage,metric in (perf.get('exclusive_stages') or {}).items(): phases[stage]+=metric.get('us',0)/1e6
        result=read(seed/'result.json') if (seed/'result.json').exists() else {}
        workers=(result.get('resources') or {}).get('workers')
        if not workers or wall<=0: continue
        # phase coverage is absorbed results only; never call the unmatched
        # transport-minus-phase difference pure overhead when counts differ.
        rows.append(dict(run=run.name,workers=workers,wall_seconds=wall,task_wall_seconds=occupied,
                         task_wall_occupancy=occupied/(workers*wall),transport_count=transport_count,
                         measured_evaluations=measured,cached_evaluations=cached,missing_performance=failures,
                         measured_native_seconds=native,exclusive_stages=dict(phases),
                         bootstrap_seconds=phases['bootstrap'],setup_restore_seconds=phases['run_setup_or_restore'],
                         replay_prefix_seconds=phases['prefix_native_execute'],evidence_write_seconds=phases['evidence_write'],
                         checkpoint_capture_seconds=phases['checkpoint_capture'],beam_seconds=phases['beam_search']))
        print(run.name,round(rows[-1]['task_wall_occupancy'],4),flush=True)
    groups=[]
    for name in ('A','A2','B','B2'):
        selected=[r for r in rows if r['run'].startswith('ab105-'+name+'-')]
        denom=sum(r['workers']*r['wall_seconds'] for r in selected)
        native=sum(r['measured_native_seconds'] for r in selected)
        phase={k:sum(r[k] for r in selected) for k in ('bootstrap_seconds','setup_restore_seconds','replay_prefix_seconds','evidence_write_seconds','checkpoint_capture_seconds','beam_seconds')}
        groups.append(dict(group=name,runs=len(selected),weighted_occupancy=sum(r['task_wall_seconds'] for r in selected)/denom if denom else None,
                           median_occupancy=statistics.median(r['task_wall_occupancy'] for r in selected) if selected else None,
                           measured_native_seconds=native,phase_seconds=phase,
                           measured_noncompute_fraction=sum(v for k,v in phase.items() if k!='beam_seconds')/native if native else None))
    a.out.write_text(json.dumps(dict(schema='spire-worker-occupancy/v1',rows=rows,groups=groups,
        definition='sum(native evaluation transport wall)/(workers*whole job wall); verification excluded; startup and result serialization included in transport',
        limitations=['Unrecorded canceled/inflight tasks lower the estimate','Whole job denominator includes verification and startup/shutdown','Phase coverage includes only absorbed successful evaluations','CPU tick accounting is not occupancy; no pure CPU-saving claim'],no_runtime_changes=True),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(groups,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
