"""Monotone best-observed damage curves from native fatal-fight artifacts.

Damage is a progress proxy, never a victory. Different boss identities/phases
remain separate; missing artifacts are reported instead of treated as zero HP.
"""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from spire_exact.planning.io import read_json, write_json
from spire_exact.planning.archive import combat_loss_progress,combat_progress_key
from tools.baseline_metrics import read_ledger


def update_curve(curves, key, row, progress, nodes):
    if not progress.get('available'):
        return
    best = curves.setdefault(key, [])
    score = combat_progress_key(progress)
    if best and score <= combat_progress_key(best[-1]):
        return
    best.append({**progress, 'progress_signature': list(score), 'label': row['label'], 'wall_seconds': row.get('completed_wall_seconds'),
                 'job_cpu_seconds': (row.get('runtime') or {}).get('job_cpu_seconds'),
                 'cumulative_expanded_nodes': nodes, 'win': False})


def observed_boss_passages(result):
    """Native route advanced beyond a boss room alive, not a full-run proof."""
    rooms = {}
    observations = [item.get('observation') or {} for item in result.get('decision_evidence') or []]
    observations.append(result.get('observation') or {})
    for obs in observations:
        act, floor = obs.get('act'), obs.get('floor')
        if act is None or floor is None:
            continue
        if float(obs.get('hp') or 0) > 0:
            for (old_act, old_floor), room in rooms.items():
                if act > old_act or act == old_act and floor > old_floor:
                    room['passed'] = True
        if obs.get('room') == 'Boss' and obs.get('enemies'):
            room = rooms.setdefault((act, floor), {'enemies': set(), 'passed': False})
            room['enemies'].update(e.get('id', '?') for e in obs['enemies'])
    return [{'act': key[0], 'floor': key[1], 'enemies': sorted(room['enemies'])}
            for key, room in rooms.items() if room['passed']]


def audit(seed_dir, out, limit=None):
    records, issues = read_ledger(seed_dir / 'evaluations.jsonl')
    if limit is not None:
        records = records[:limit]
    curves = {}; passages = {}; nodes = 0; checked = 0; unavailable = []; errors = []
    for row in records:
        if not row.get('cache_hit'):
            nodes += row.get('expanded_combat_nodes') or 0
        obs = row.get('observation') or {}
        if row.get('classification') not in ('NATIVE_ROUTE_DEATH', 'NATIVE_WIN_CANDIDATE', 'SEARCH_BUDGET', 'DECISION_BOUNDARY'):
            continue
        try:
            folder = seed_dir / row['label']
            result = (read_json(folder / 'cached.json')['result'] if (folder / 'cached.json').exists()
                      else read_json(folder / 'data/decision.json'))
            for passage in observed_boss_passages(result):
                key = json.dumps(passage, sort_keys=True)
                passages.setdefault(key, {**passage, 'label': row['label'],
                    'wall_seconds': row.get('completed_wall_seconds'),
                    'job_cpu_seconds': (row.get('runtime') or {}).get('job_cpu_seconds'),
                    'cumulative_expanded_nodes': nodes,
                    'evidence': 'native route advanced beyond boss room alive; not a whole-game win certificate'})
            if row.get('classification') != 'NATIVE_ROUTE_DEATH' or obs.get('room') != 'Boss':
                continue
            progress = combat_loss_progress(result); checked += 1
            if not progress['available']:
                unavailable.append(row['label']); continue
            # Use all enemy model identities observed at the actual fatal room.
            # This avoids equating distinct boss fights by floor alone.
            enemies = set()
            for item in result.get('decision_evidence') or []:
                current = item.get('observation') or {}
                if current.get('floor') == obs.get('floor') and current.get('act') == obs.get('act'):
                    enemies.update(e.get('id', '?') for e in current.get('enemies') or [])
            key = json.dumps([obs.get('act'), obs.get('floor'), sorted(enemies)], separators=(',', ':'))
            update_curve(curves, key, row, progress, nodes)
        except (OSError, ValueError, KeyError) as error:
            errors.append({'label': row['label'], 'error': str(error)})
    report = {'source': str(seed_dir.resolve()), 'evaluation_prefix_count': len(records),
              'partial_snapshot': limit is not None, 'boss_deaths_checked': checked,
              'curves': [{'act': json.loads(key)[0], 'floor': json.loads(key)[1], 'enemies': json.loads(key)[2],
                          'improvements': values} for key, values in curves.items()],
              'unavailable': unavailable, 'artifact_errors': errors, 'ledger_issues': issues,
              'passed_gate_milestones': list(passages.values()),
              'curves_cover_fatal_fights_only': True,
              'metric_is_observed_damage_proxy': True, 'no_victory_inferred': True,
              'time_means_ordered_absorption_not_physical_worker_completion': True}
    write_json(out, report)
    print(json.dumps({'boss_deaths_checked': checked, 'errors': len(errors), 'passed_gate_milestones': list(passages.values()), 'curves': [
        {'floor': c['floor'], 'enemies': c['enemies'], 'improvements': len(c['improvements']),
         'last': c['improvements'][-1]} for c in report['curves']]}), flush=True)
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--seed-dir', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True); p.add_argument('--limit', type=int)
    a = p.parse_args()
    if a.limit is not None and a.limit < 1:
        p.error('Positive evaluation prefix length required')
    audit(a.seed_dir, a.out, a.limit)
