"""Full exported-byte and conservative clock audit of completed infra comparisons."""
import argparse
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.fight_bench import GAME_FIELDS, algorithm_request, diagnostic_request
from spire_exact.canonical import canonical
from spire_exact.planning.io import read_json,write_json
from spire_exact.planning.budget_audit import clock_gate_audit


def audit_pair(a,b,allow_solver_change=False,allow_diagnostic_change=False):
    first=sorted(a.glob('case-*/request.json'))
    second=sorted(b.glob('case-*/request.json'))
    if [p.parent.name for p in first]!=[p.parent.name for p in second] or not first:
        raise ValueError('Case sets differ or are empty')
    checks=[]
    for left,right in zip(first,second):
        requests=[read_json(p)for p in(left,right)]
        for request in requests:request.pop('out',None)
        if allow_diagnostic_change:requests=[diagnostic_request(r,allow_solver_change=allow_solver_change)for r in requests]
        elif allow_solver_change:requests=[algorithm_request(r)for r in requests]
        results=[read_json(p.parent/'data/decision.json')for p in(left,right)]
        metrics=[(r.get('advisor_metrics')or{}).get('searches',[])for r in results]
        row={'case':left.parent.name,'request_bytes':canonical(requests[0])==canonical(requests[1]),
             'game_bytes':canonical({k:results[0].get(k)for k in GAME_FIELDS})==canonical({k:results[1].get(k)for k in GAME_FIELDS}),
             'nodes':sum(s.get('expanded_nodes',0)for s in metrics[0])==sum(s.get('expanded_nodes',0)for s in metrics[1]),
             'supported':all(r.get('status')in('TERMINAL','BUDGET','DECISION')for r in results),
             'clock_gates_excluded':all(metrics)and all(clock_gate_audit(s)['clock_gates_excluded']for ss in metrics for s in ss),
             'offline':all((r.get('advisor_metrics')or{}).get('counters',{}).get('offline_statistics_disabled',0)>0 for r in results)}
        checks.append(row)
    return {'left':str(a),'right':str(b),'rows':checks,
            'passed':all(all(v for k,v in row.items()if k!='case')for row in checks)}


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    if a.out.exists():raise ValueError('Fresh report required')
    from tools.fight_bench import _affinity
    _affinity('e')
    pairs=[]
    for round_id in range(2):
        for arm in('bounded32','recycle64'):
            pairs.append(audit_pair(a.root/f'profiles-p7/r{round_id}-large32',a.root/f'profiles-p7/r{round_id}-{arm}'))
        pairs.append(audit_pair(a.root/f'solver-p7-audited/r{round_id}-vendor',a.root/f'solver-p7-audited/r{round_id}-solver1',True))
    pairs.append(audit_pair(a.root/'solver-p7-audited/r0-vendor',a.root/'rebuilt-contract-final',True))
    report={'passed':all(p['passed']for p in pairs),'pairs':pairs,'paired_cases':sum(len(p['rows'])for p in pairs),
            'scope':'full exported game bytes, not feature/hash-only state deduplication; no performance promotion'}
    write_json(a.out,report);print(json.dumps({'passed':report['passed'],'paired_cases':report['paired_cases']}))
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
