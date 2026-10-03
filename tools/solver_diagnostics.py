"""Accounting helpers for native performance evidence; no search/game mutations."""
from collections import Counter
import json
from pathlib import Path

from spire_exact.canonical import canonical
from spire_exact.planning.io import read_json
from spire_exact.planning.budget_audit import clock_gate_audit
from tools.fight_bench import GAME_FIELDS


def raw_cases(folder):
    folder=Path(folder)
    compact=[json.loads(line)for line in(folder/'results.jsonl').read_text(encoding='utf-8').splitlines()]
    manifest=read_json(folder/'manifest.json')
    document=read_json(Path(manifest['cases']))
    indices={case['id']:i for i,case in enumerate(document['cases'])}
    rows=[]
    for row in compact:
        target=folder/f"case-{indices[row['case']]:04d}-r{row['repeat']}"
        decision=read_json(target/'data/decision.json')
        transport=read_json(target/'transport.json')
        searches=(decision.get('advisor_metrics')or{}).get('searches',[])
        phases={}
        for search in searches:
            for name,metrics in(search.get('phase_metrics')or{}).items():
                value=phases.setdefault(name,{'wall_us':0,'allocated_bytes':0})
                for key in value:value[key]+=metrics[key]
        perf=decision.get('performance')or{}
        rows.append({**row,'path':str(target),'search_wall_us_exact':sum(s['wall_us']for s in searches),
                     'allocated_bytes_exact':sum(s['allocated_bytes']for s in searches),
                     'gc_pause_ms_exact':sum(s['gc_pause_ms']for s in searches),
                     'search_count':len(searches),'native_wall_us':perf.get('wall_us',0),
                     'native_stages':perf.get('exclusive_stages')or{},'phase_metrics_exact':phases,
                     'worker_job':transport['worker_job'],'worker_pid':transport['pid'],
                     'transport_wall':transport['wall_seconds'],'transport_queue':transport.get('queue_seconds'),
                     'reason':decision.get('reason'),'observation':decision.get('observation'),
                     'clock_all_excluded':all(clock_gate_audit(s)['clock_gates_excluded']for s in searches),
                     'search_details':searches})
    return rows


def totals(rows):
    search=sum(r['search_wall_us_exact']for r in rows)/1e6
    native=sum(r['native_wall_us']for r in rows)/1e6
    nodes=sum(r['nodes']for r in rows)
    allocation=sum(r['allocated_bytes_exact']for r in rows)
    stages={}
    native_stages=Counter()
    for r in rows:
        for name,v in r['phase_metrics_exact'].items():
            d=stages.setdefault(name,{'seconds':0.,'allocated_bytes':0})
            d['seconds']+=v['wall_us']/1e6;d['allocated_bytes']+=v['allocated_bytes']
        for name,v in r['native_stages'].items():native_stages[name]+=v['us']/1e6
    for v in stages.values():
        v['search_fraction']=v['seconds']/search if search else None
        v['allocation_fraction']=v['allocated_bytes']/allocation if allocation else None
    return {'cases':len(rows),'nodes':nodes,'search_seconds':search,'native_seconds':native,
            'search_fraction_of_native':search/native if native else None,
            'nodes_per_search_second':nodes/search if search else None,'allocation_bytes':allocation,
            'allocation_GiB':allocation/2**30,'bytes_per_node':allocation/nodes if nodes else None,
            'gc_pause_seconds':sum(r['gc_pause_ms_exact']for r in rows)/1000,
            'gc_pause_fraction':sum(r['gc_pause_ms_exact']for r in rows)/(search*1000)if search else None,
            'phases_exclusive':stages,'native_stages_exclusive_seconds':dict(native_stages),
            'phase_fraction_sum':sum(v['seconds']for v in stages.values())/search if search else None}


def compare_full(left,right,ignore_phase_measurement=False):
    left,right=Path(left),Path(right)
    paths=sorted(left.glob('case-*/request.json'))
    other=sorted(right.glob('case-*/request.json'))
    if not paths or [p.parent.name for p in paths]!=[p.parent.name for p in other]:
        return {'passed':False,'reason':'case sets differ'}
    checks=[]
    for pa,pb in zip(paths,other):
        reqs=[read_json(p)for p in(pa,pb)]
        for request in reqs:
            request.pop('out',None)
            if ignore_phase_measurement:request.get('advisor',{}).pop('measure_search_phases',None)
        ds=[read_json(p.parent/'data/decision.json')for p in(pa,pb)]
        ss=[(d.get('advisor_metrics')or{}).get('searches',[])for d in ds]
        check={'case':pa.parent.name,'request':canonical(reqs[0])==canonical(reqs[1]),
               'game':canonical({k:ds[0].get(k)for k in GAME_FIELDS})==canonical({k:ds[1].get(k)for k in GAME_FIELDS}),
               'node_sequence':[s['expanded_nodes']for s in ss[0]]==[s['expanded_nodes']for s in ss[1]],
               'clock_excluded':all(ss)and all(clock_gate_audit(s)['clock_gates_excluded']for searches in ss for s in searches),
               'offline':all((d.get('advisor_metrics')or{}).get('counters',{}).get('offline_statistics_disabled',0)>0 for d in ds)}
        checks.append(check)
    return {'passed':all(all(v for k,v in r.items()if k!='case')for r in checks),'pairs':len(checks),'checks':checks,
            'ignored_request_fields':['out']+(['advisor.measure_search_phases']if ignore_phase_measurement else[])}


def stack_profile(data):
    names=[f['name']for f in data['shared']['frames']]
    exclusive=Counter();inclusive=Counter();kinds=Counter();total=0.;thread_rows=[]
    pseudo={'CPU_TIME','UNMANAGED_CODE_TIME','BLOCKED_TIME'}
    for profile in data['profiles']:
        if profile['type']!='evented' or profile['unit']!='milliseconds':raise ValueError('Unsupported profile schema/units')
        stack=[];depth=0;previous=profile['startValue'];thread_time=0.
        for event in profile['events']:
            dt=event['at']-previous
            if dt < -1e-6:raise ValueError('Nonmonotonic stack events')
            if dt>0 and depth and stack:
                frames=[names[f]for f in stack];real=[n for n in frames if n not in pseudo]
                total+=dt;thread_time+=dt
                kinds[frames[-1]if frames[-1]in pseudo else 'unclassified']+=dt
                if real:exclusive[real[-1]]+=dt
                for name in set(real):inclusive[name]+=dt
            index=event['frame'];inside='CombatBeamSolver.SolveCore('in names[index]
            if event['type']=='O':stack.append(index);depth+=inside
            elif event['type']=='C':
                if not stack or stack.pop()!=index:raise ValueError('Unbalanced stack')
                depth-=inside
            else:raise ValueError('Unknown stack event')
            previous=event['at']
        if stack:raise ValueError('Unclosed stack')
        if thread_time:thread_rows.append({'name':profile['name'],'search_sample_ms':thread_time})
    rows=lambda values:[{'name':k,'ms':v,'percent':v/total*100 if total else None}for k,v in values.most_common()]
    return {'search_sample_ms':total,'threads':thread_rows,'sample_kind_ms':dict(kinds),
            'exclusive_last_resolved_frame':rows(exclusive),'inclusive':rows(inclusive),
            'scope':'conditioned on SolveCore; inclusive overlaps; exclusive native/unknown time stays at last resolved caller; sampled thread time is not CPU utilization'}
