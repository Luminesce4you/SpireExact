"""Read-only progress summary of one seed directory (evaluations.jsonl + result.json).

Milestones are observed native progress, never a win claim: only certificate.json written after
an independent fresh replay counts as a win.
"""
from pathlib import Path
from collections import Counter
import argparse, json, sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load(path):
    rows = []
    with open(path, encoding='utf-8') as stream:
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try: rows.append(json.loads(line))
            except ValueError: break          # a line still being written
    return rows


def summarize(seed: Path, bucket: int = 300):
    rows = load(seed / 'evaluations.jsonl')
    out = {'evaluations': len(rows)}
    if not rows:
        return out
    wall = max(r.get('completed_wall_seconds') or 0 for r in rows)
    out['absorbed_wall_seconds'] = round(wall, 1)
    out['classifications'] = dict(Counter(r.get('classification') for r in rows))
    out['kinds'] = dict(Counter(r.get('kind') for r in rows))
    floors = Counter()
    first = {}
    for r in rows:
        obs = r.get('observation') or {}
        floor = obs.get('floor')
        if floor is None:
            continue
        floors[(floor, obs.get('room'))] += 1
        if floor not in first:
            first[floor] = (round(r.get('completed_wall_seconds') or 0, 1), r['label'])
    out['terminal_floor_histogram'] = {f'{f}:{room}': n for (f, room), n in sorted(floors.items(), key=lambda kv: (kv[0][0], str(kv[0][1])))}
    best = max(first)
    out['furthest_floor'] = best
    out['first_time_at_floor'] = {str(f): first[f] for f in sorted(first) if f >= best - 2}
    task_wall = sum((r.get('performance') or {}).get('wall_us') or 0 for r in rows) / 1e6
    nodes = sum(r.get('expanded_combat_nodes') or 0 for r in rows)
    runtime = rows[-1].get('runtime') or {}
    out['native_task_wall_seconds'] = round(task_wall, 1)
    out['expanded_combat_nodes'] = nodes
    if runtime.get('job_wall_seconds'):
        out['job_wall_seconds'] = round(runtime['job_wall_seconds'], 1)
        out['tick_sampled_busy_cores'] = round(runtime['job_cpu_seconds'] / runtime['job_wall_seconds'], 2)
        out['peak_job_commit_gib'] = round(runtime['peak_job_commit_bytes'] / 2 ** 30, 2)
    # Worker occupancy from native task wall: the tick-sampled Job CPU under-reports a search
    # thread that yields every 4 ms (measured against the cycle counter on 2026-10-01).
    workers = None
    result_path = seed / 'result.json'
    if result_path.exists():
        try: result = json.loads(result_path.read_text(encoding='utf-8'))
        except ValueError: result = {}
        workers = (result.get('resources') or {}).get('workers')
        out['status'] = result.get('status'); out['best_label'] = result.get('best_label')
        metrics = result.get('search_metrics') or {}
        for key in ('scheduler', 'focus_evaluations', 'explorer_evaluations', 'pending_branches', 'pending_repairs', 'elites', 'best_trajectory_changes'):
            if key in metrics: out[key] = metrics[key]
        gates = (result.get('gate_models') or {}).get('gates') or []
        out['gates'] = [{k: g.get(k) for k in ('act', 'ordinal', 'encounter', 'entries', 'survived_entries', 'best_outcome', 'fitted_entries', 'fitted_pairs')} for g in gates]
    if workers and wall:
        out['worker_occupancy'] = round(task_wall / (workers * wall), 3)
    out['certificate'] = (seed / 'certificate.json').exists()
    timeline = Counter()
    for r in rows:
        timeline[int((r.get('completed_wall_seconds') or 0) // bucket)] += 1
    out[f'evaluations_per_{bucket}s'] = [timeline[i] for i in range(max(timeline) + 1)]
    return out


def main():
    p = argparse.ArgumentParser(); p.add_argument('seed', type=Path); p.add_argument('--bucket', type=int, default=300)
    a = p.parse_args()
    print(json.dumps(summarize(a.seed, a.bucket), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
