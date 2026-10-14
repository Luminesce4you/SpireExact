"""T4: worker occupancy and per-evaluation overhead of iteration-105, from wall clocks (read-only).

occupancy = sum of per-evaluation worker wall (transport.json wall_seconds) / (workers x run wall).
Run wall is the coordinator's elapsed_seconds (result.json). Host time is split by the host's own
exclusive stages (data/performance.json): search, new-action execution, and the non-search overhead
(bootstrap, run setup / checkpoint restore, prefix replay, checkpoint capture, evidence write, menu
enumeration). Transport wall minus host wall is the process / pipe / file overhead outside the host.
CPU tick samples are not used (AGENTS 1.1 item 4: they under-count).

Usage: python tools/worker_occupancy.py [--runs DIR] [--out FILE]
"""
from pathlib import Path
import argparse
import json
import os
import statistics

SEARCH = ('beam_search', 'combat_advisor', 'continuation_compare', 'candidate_select')
EXECUTE = ('new_native_execute', 'settle')
OVERHEAD = ('bootstrap', 'run_setup_or_restore', 'prefix_native_execute', 'checkpoint_capture', 'evidence_write',
            'legal_menu')


def load(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def run_row(run):
    seed = next(run.glob('seed-*/result.json'), None)
    result = load(seed) if seed else None
    if not result:
        return None
    workers = (result.get('resources') or {}).get('workers')
    wall = result.get('elapsed_seconds')
    evals = sorted(p for p in seed.parent.glob('eval-*') if p.is_dir())
    totals = dict.fromkeys(('transport', 'host', 'queue', 'search', 'execute', 'overhead', 'other'), 0.0)
    stage_totals, n, missing = {}, 0, 0
    for e in evals:
        t, perf = load(e / 'transport.json'), load(e / 'data/performance.json')
        if not t or not perf:
            missing += 1; continue
        n += 1
        stages = {k: v.get('us', 0) / 1e6 for k, v in (perf.get('exclusive_stages') or {}).items()}
        host = perf['wall_us'] / 1e6
        totals['transport'] += t['wall_seconds']; totals['host'] += host; totals['queue'] += t.get('queue_seconds') or 0
        for k, v in stages.items():
            stage_totals[k] = stage_totals.get(k, 0) + v
        s = sum(stages.get(k, 0) for k in SEARCH); x = sum(stages.get(k, 0) for k in EXECUTE)
        o = sum(stages.get(k, 0) for k in OVERHEAD)
        totals['search'] += s; totals['execute'] += x; totals['overhead'] += o; totals['other'] += host - s - x - o
    if not workers or not wall or not n:
        return None
    return {'run': run.name, 'workers': workers, 'wall': wall, 'evaluations': n, 'missing': missing,
            'recorded_total': result.get('evaluations_total'), **totals, 'stages': stage_totals,
            'occupancy': totals['transport'] / (workers * wall), 'host_occupancy': totals['host'] / (workers * wall)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runs', type=Path, default=Path(__file__).resolve().parents[2] / 'iteration-105-ablation-runs')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if os.name == 'nt':
        import ctypes
        k = ctypes.WinDLL('kernel32'); k.GetCurrentProcess.restype = ctypes.c_void_p
        k.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        k.SetProcessAffinityMask(k.GetCurrentProcess(), 0xFFFF0000)   # E cores
    rows = [r for r in (run_row(p) for p in sorted(args.runs.iterdir()) if p.is_dir()) if r]
    def med(key): return statistics.median(r[key] for r in rows)
    agg = {k: sum(r[k] for r in rows) for k in ('transport', 'host', 'search', 'execute', 'overhead', 'other', 'queue')}
    stages = {}
    for r in rows:
        for k, v in r['stages'].items():
            stages[k] = stages.get(k, 0) + v
    summary = {
        'runs': len(rows), 'evaluations': sum(r['evaluations'] for r in rows),
        'missing_evaluation_files': sum(r['missing'] for r in rows),
        'occupancy_median': med('occupancy'), 'occupancy_pooled': agg['transport'] / sum(r['workers'] * r['wall'] for r in rows),
        'host_occupancy_pooled': agg['host'] / sum(r['workers'] * r['wall'] for r in rows),
        'share_of_worker_wall': {k: agg[k] / agg['transport'] for k in ('search', 'execute', 'overhead', 'other')}
        | {'outside_host': (agg['transport'] - agg['host']) / agg['transport']},
        'overhead_stages_share_of_worker_wall': {k: stages.get(k, 0) / agg['transport'] for k in OVERHEAD},
        'queue_seconds_per_evaluation': agg['queue'] / sum(r['evaluations'] for r in rows),
    }
    args.out.write_text(json.dumps({'schema': 'spire-worker-occupancy/v1', 'summary': summary, 'runs': rows},
                                   ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
