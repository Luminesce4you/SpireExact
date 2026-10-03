"""Read exact parent/probe pairs; do not equate projected state features."""
from pathlib import Path
import argparse
import collections
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from spire_exact.planning.io import read_json, write_json
from spire_exact.planning.archive import combat_loss_progress, combat_progress_key, classify_failure, failure_combat_prefix
from spire_exact.canonical import canonical
from tools.baseline_metrics import read_ledger


def audit(seed_dir, out, limit=None):
    records, issues = read_ledger(seed_dir / 'evaluations.jsonl')
    if limit is not None:
        records = records[:limit]
    pairs = []
    for record in records:
        if record.get('kind') != 'combat_probe':
            continue
        row = {'label': record['label'], 'source': (record.get('repair') or {}).get('source')}
        try:
            if not row['source']:
                raise ValueError('Missing original source label')
            parent = read_json(seed_dir / row['source'] / 'data/decision.json')
            probe = read_json(seed_dir / row['label'] / 'data/decision.json')
            request = read_json(seed_dir / row['label'] / 'request.json')
            entry = failure_combat_prefix(parent)
            if entry is None:
                raise ValueError('Parent has no observed fatal entry')
            n = len(entry['prefix'])
            evidence = probe.get('decision_evidence') or []
            exact_entry = (canonical(request['history']) == canonical(entry['prefix'])
                           and canonical(probe.get('trace', [])[:n]) == canonical(entry['prefix'])
                           and len(evidence) > n
                           and canonical(evidence[n]['observation']) == canonical(entry['entry_observation']))
            parent_loss, probe_loss = combat_loss_progress(parent), combat_loss_progress(probe)
            obs = probe.get('observation') or {}
            same_floor = obs.get('floor') == entry['floor'] and obs.get('act') == entry['entry_observation'].get('act')
            comparisons_available = (exact_entry and same_floor and classify_failure(probe) == 'NATIVE_ROUTE_DEATH'
                                     and parent_loss['available'] and probe_loss['available'])
            searches = (probe.get('advisor_metrics') or {}).get('searches') or []
            row.update(
                exact_entry=exact_entry, floor=entry['floor'], act=entry['entry_observation'].get('act'),
                entry_enemies=[e['id'] for e in entry['entry_observation'].get('enemies', [])],
                classification=classify_failure(probe), same_trace=canonical(parent['trace']) == canonical(probe['trace']),
                requested_nodes=(request.get('advisor') or {}).get('nodes'),
                search_count=len(searches), probe_expanded_nodes=sum(s.get('expanded_nodes') or 0 for s in searches),
                max_search_nodes=max((s.get('expanded_nodes') or 0 for s in searches), default=0),
                search_boundaries=dict(collections.Counter(str(s.get('boundary')) for s in searches)),
                native_service_seconds=(probe.get('performance') or {}).get('wall_us', 0) / 1e6,
                parent_loss_progress=parent_loss, probe_loss_progress=probe_loss,
                comparable_death_progress=comparisons_available,
                phase_aware_progress_comparison=((combat_progress_key(probe_loss)>combat_progress_key(parent_loss))-
                    (combat_progress_key(probe_loss)<combat_progress_key(parent_loss)))if comparisons_available else None,
                hp_removed_fraction_delta=(probe_loss['hp_removed_fraction'] - parent_loss['hp_removed_fraction'])
                    if comparisons_available and probe_loss.get('revivals_observed',0)==parent_loss.get('revivals_observed',0) else None)
        except (OSError, ValueError, KeyError) as error:
            row['error'] = str(error)
        pairs.append(row)
    valid = [p for p in pairs if p.get('exact_entry') and 'error' not in p]
    deltas = [p['hp_removed_fraction_delta'] for p in valid if p['hp_removed_fraction_delta'] is not None]
    phase_comparisons=[p['phase_aware_progress_comparison']for p in valid if p.get('phase_aware_progress_comparison')is not None]
    all_service = sum((r.get('performance') or {}).get('wall_us', 0) / 1e6
                      for r in records if not r.get('cache_hit'))
    probe_service = sum(p['native_service_seconds'] for p in valid)
    summary = {
        'source': str(seed_dir.resolve()), 'evaluation_prefix_count': len(records),
        'snapshot_is_partial': limit is not None, 'ledger_issues': issues,
        'pairs': pairs, 'pair_count': len(pairs), 'valid_exact_entries': len(valid),
        'same_full_trace': sum(p['same_trace'] for p in valid),
        'outcomes': dict(collections.Counter(p['classification'] for p in valid)),
        'damage_improved': sum(d > 0 for d in deltas), 'damage_equal': sum(d == 0 for d in deltas),
        'damage_worse': sum(d < 0 for d in deltas),
        'damage_fraction_deltas_compare_same_observed_life_count_only':True,
        'phase_progress_improved':sum(d>0 for d in phase_comparisons),
        'phase_progress_equal':sum(d==0 for d in phase_comparisons),
        'phase_progress_worse':sum(d<0 for d in phase_comparisons),
        'all_searches_below_requested_node_cap': sum(p['search_count'] > 0 and p.get('requested_nodes') is not None
            and p['max_search_nodes'] < p['requested_nodes'] for p in valid),
        'native_service_seconds_sum': probe_service,
        'all_absorbed_native_service_seconds_sum': all_service,
        'probe_fraction_of_native_service': probe_service / all_service if all_service else None,
        'below_cap_does_not_mean_exhaustive': True,
        'not_independent_samples': True, 'not_a_seed_infeasibility_proof': True,
        'scope': 'same complete entry prefix and exported native observation; paired diagnostic only, no new replay execution',
    }
    write_json(out, summary)
    print(json.dumps({k: v for k, v in summary.items() if k != 'pairs'}), flush=True)
    return summary


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--seed-dir', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True); p.add_argument('--limit', type=int)
    a = p.parse_args()
    if a.limit is not None and a.limit < 1:
        p.error('Positive prefix length required')
    audit(a.seed_dir, a.out, a.limit)
