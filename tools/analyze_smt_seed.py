"""Read paired campaign evidence for one SMT experiment, without changing it."""
import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from spire_exact.canonical import canonical
from spire_exact.planning.budget_audit import clock_gate_audit
from tools.fight_bench import GAME_FIELDS
from tools.compare_runs import summarize


def load(path):
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt', encoding='utf-8-sig') as stream:
        return json.load(stream)


def arm(folder):
    summary = summarize(folder)
    seed = folder / ('seed-' + summary['seed'])
    rows = [json.loads(line) for line in (seed/'evaluations.jsonl').read_text(encoding='utf-8').splitlines()]
    manifest = load(folder/'validation-manifest.json')
    result = load(seed/'result.json')
    first_win=next((i for i,r in enumerate(rows) if r.get('classification')=='NATIVE_WIN_CANDIDATE'),None)
    active=rows[:first_win+1] if first_win is not None else [r for r in rows if r.get('classification')!='SEARCH_CANCELLED']
    native = [r for r in active if not r.get('cache_hit') and r.get('kind') != 'gate_probe']
    searches = [s for r in native for s in (r.get('advisor_metrics') or {}).get('searches', [])]
    seconds = sum((r.get('performance') or {}).get('wall_us', 0) for r in native)/1e6
    wall = max((r.get('completed_wall_seconds', 0) for r in active), default=0)
    search_wall = sum(s.get('wall_us', 0) for s in searches)/1e6
    nodes = sum(r.get('expanded_combat_nodes', 0) for r in native)
    reserve = result['resources']['workers']
    summary.update(
        cpu_set=manifest['cpu_set'], resources=result['resources'], runtime=result.get('native_runtime'),
        submitted_records=len(rows), evaluations=len(active),
        cancelled_after_win_records=sum(r.get('classification')=='SEARCH_CANCELLED' for r in rows),
        speculative_completed_after_win=len(rows)-len(active)-sum(r.get('classification')=='SEARCH_CANCELLED' for r in rows),
        native_evaluations=len(native), observed_wall_seconds=wall,
        verified_completion_seconds=result.get('elapsed_seconds') if summary['verified_win'] else None,
        evaluations_per_hour=len(active)*3600/wall if wall else None,
        combat_nodes=nodes, combat_nodes_per_wall_second=nodes/wall if wall else None,
        native_service_seconds=seconds, worker_occupancy=seconds/(reserve*wall) if wall else None,
        search_seconds=search_wall, nodes_per_search_second=nodes/search_wall if search_wall else None,
        gc_pause_seconds=sum(s.get('gc_pause_ms', 0) for s in searches)/1000,
        gc_pause_fraction=sum(s.get('gc_pause_ms', 0) for s in searches)/(1000*search_wall) if search_wall else None,
        clock_audited_searches=len(searches), clock_unexcluded_searches=sum(not clock_gate_audit(s)['clock_gates_excluded'] for s in searches),
        failures=dict(Counter(r.get('classification') for r in native if r.get('classification') not in ('NATIVE_ROUTE_DEATH', 'NATIVE_WIN_CANDIDATE'))),
    )
    return summary, active, seed


def compare(left, right):
    left,right=Path(left).resolve(),Path(right).resolve()
    a, ar, ap = arm(left)
    b, br, bp = arm(right)
    # Initial root tasks are physically recorded on completion, then sorted for
    # absorption. Match submission labels, not ledger positions.
    left_by_label={r['label']:r for r in ar}
    right_by_label={r['label']:r for r in br}
    if len(left_by_label)!=len(ar) or len(right_by_label)!=len(br):
        raise ValueError('Duplicate evaluation labels')
    pairs = []
    labels=sorted(left_by_label.keys()&right_by_label.keys(),key=lambda k:int(k.split('-')[1]))
    for label in labels:
        ra,rb=left_by_label[label],right_by_label[label]
        label = ra['label']
        row = {'left_label': label, 'right_label': rb['label'], 'same_label': label == rb['label'],
               'classification_equal': ra.get('classification') == rb.get('classification'),
               'observation_equal': canonical(ra.get('observation')) == canonical(rb.get('observation')),
               'nodes_equal': ra.get('expanded_combat_nodes') == rb.get('expanded_combat_nodes'),
               'left_seconds': ra.get('completed_wall_seconds'), 'right_seconds': rb.get('completed_wall_seconds')}
        decisions = []
        for parent, r in ((ap, ra), (bp, rb)):
            path = parent/r['label']/'data/decision.json'
            if not path.exists(): path = path.with_suffix('.json.gz')
            decisions.append(load(path) if path.exists() else None)
        requests=[]
        for parent,r in ((ap,ra),(bp,rb)):
            path=parent/r['label']/'request.json'
            if path.exists():
                request=load(path);request.pop('out',None)
                if request.get('checkpoint'):
                    request['checkpoint']=str(Path(request['checkpoint']).relative_to(parent))
                requests.append(request)
        if len(requests)==2:
            row['algorithm_request_equal']=canonical(requests[0])==canonical(requests[1])
        if all(d is not None for d in decisions):
            hashes = [hashlib.sha256(canonical({k:d.get(k) for k in GAME_FIELDS})).hexdigest() for d in decisions]
            row.update(game_bytes_equal=hashes[0] == hashes[1], game_sha256=hashes)
        pairs.append(row)
    ratio = a['observed_wall_seconds']/b['observed_wall_seconds'] if b['observed_wall_seconds'] else None
    return {'schema':'spire-smt-campaign-comparison/v1', 'arms':[a,b], 'paired_evaluations':pairs,
            'common_prefix_length':len(pairs), 'all_common_game_bytes_equal':all(r.get('game_bytes_equal') is True for r in pairs) if pairs else False,
            'ledger_completion_order_equal':[r['label'] for r in ar]==[r['label'] for r in br],
            'wall_speedup':ratio if len(ar) == len(br) and a['verified_win'] == b['verified_win'] else None,
            'scope':'One seed, one run per arm; SMT and bounded-GC configuration changed jointly. This does not isolate SMT or establish reliability.',
            'normal_godot_verified':False}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('left', type=Path); p.add_argument('right', type=Path); p.add_argument('--out', type=Path, required=True)
    a=p.parse_args()
    report=compare(a.left,a.right)
    a.out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    for row in report['arms']:
        print(json.dumps({k:row[k] for k in ('run','verified_win','wall_seconds','evaluations','resources','worker_occupancy','combat_nodes_per_wall_second','gc_pause_fraction','clock_unexcluded_searches','failures')},ensure_ascii=False))


if __name__ == '__main__': main()
