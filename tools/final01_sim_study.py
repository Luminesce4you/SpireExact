"""Paired synthetic-campaign study of planner arms. NOT native evidence.

Runs the real planner loop (tools/sim_campaign) for every (world, solver seed,
arm) of a predeclared panel at one simulated budget, then reports solved
counts at 30 and 45 simulated minutes, restricted mean time, late-half survival
area and paired differences against a reference arm, per world class and
pooled. The development panel (seeds 2000+) is for tuning; the evaluation
panels (eval: seeds 1000+, evalhard: 4000+) are run once on frozen code and
reported in full. Simulated seconds are a cost model.

    python tools/final01_sim_study.py run --panel eval --out outputs/final01-sim-eval.jsonl
    python tools/final01_sim_study.py report --runs outputs/final01-sim-eval.jsonl --out outputs/final01-sim-eval.json
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.sim_campaign import ARMS, MIX, World, simulate  # noqa: E402

PANELS = {'eval': 1000, 'dev': 2000, 'devhard': 3000, 'evalhard': 4000}
# Long-tail panels: only the classes whose wins need re-rooting or depth.
HARD_MIX = (('resource', 8), ('root_act2', 8), ('root_act1', 8), ('mixed', 8), ('tactical', 8))
# Predeclared evaluation arms: the three versions, final01 and its main ablations.
DEFAULT_ARMS = ('i054a', 'i082', 'i085', 'i085-tail', 'final01', 'final01-noclinic', 'final01-nohint',
                'final01-noprior', 'final01-i081')
SOLVER_SEEDS = (271828, 314159)


def panel(name, count=None):
    start = PANELS[name]
    rows = []
    for kind, number in (HARD_MIX if name.endswith('hard') else MIX):
        for _ in range(number):
            rows.append((start + len(rows), kind))
    return rows if count is None else rows[:count]


def _one(job):
    seed, kind, arm, solver_seed, minutes = job
    started = time.time()
    try:
        summary = simulate(World(seed, kind), arm, minutes=minutes, solver_seed=solver_seed)
        summary['solver_seed'] = solver_seed
        summary['python_seconds'] = round(time.time() - started, 2)
        return summary
    except Exception as error:  # retained, never silently dropped
        return {'world': seed, 'kind': kind, 'arm': arm, 'solver_seed': solver_seed, 'minutes': minutes,
                'error': repr(error), 'verified': False, 'verified_seconds': None,
                'python_seconds': round(time.time() - started, 2)}


def run(args):
    jobs = [(seed, kind, arm, solver, args.minutes) for seed, kind in panel(args.panel, args.count)
            for solver in SOLVER_SEEDS[:args.solver_seeds] for arm in args.arms]
    out = Path(args.out)
    done = set()
    if out.exists():
        for line in out.read_text(encoding='utf-8').splitlines():
            row = json.loads(line)
            done.add((row['world'], row['arm'], row['solver_seed']))
    jobs = [job for job in jobs if (job[0], job[2], job[3]) not in done]
    out.parent.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(max_workers=args.processes) as executor, out.open('a', encoding='utf-8') as sink:
        for summary in executor.map(_one, jobs):
            sink.write(json.dumps(summary, sort_keys=True) + '\n')
            sink.flush()
            print(json.dumps({k: summary.get(k) for k in ('world', 'kind', 'arm', 'solver_seed', 'verified',
                                                          'verified_seconds', 'evaluations', 'python_seconds', 'error')}),
                  flush=True)


def wilson(successes, total, z=1.96):
    if not total:
        return None
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [round(centre - half, 3), round(centre + half, 3)]


def metrics(rows, cap):
    times = [row['verified_seconds'] if row['verified'] and row['verified_seconds'] is not None
             and row['verified_seconds'] <= cap else None for row in rows]
    solved = [t for t in times if t is not None]
    restricted = [t if t is not None else cap for t in times]
    late = [max(0.0, min(t if t is not None else cap, cap) - cap / 2) / (cap / 2) for t in times]
    ordered = sorted(restricted)
    return {'runs': len(rows), 'solved': len(solved), 'solved_share': round(len(solved) / len(rows), 3) if rows else None,
            'wilson95': wilson(len(solved), len(rows)), 'restricted_mean_seconds': round(sum(restricted) / len(rows), 1) if rows else None,
            'late_half_area': round(sum(late) / len(rows), 3) if rows else None,
            'median_seconds': round(ordered[len(ordered) // 2], 1) if ordered and len(solved) * 2 > len(rows) else None,
            'errors': sum(1 for row in rows if row.get('error'))}


def restricted(row, cap):
    return row['verified_seconds'] if row['verified'] and row['verified_seconds'] is not None \
        and row['verified_seconds'] <= cap else cap


def paired_block(rows, arm, reference, cap, resamples=2000):
    """Paired restricted-time differences of `arm` against `reference` on the
    same (world, solver seed), with a 95% bootstrap interval that resamples
    worlds (both solver seeds of a world move together)."""
    import random
    base = {(r['world'], r['solver_seed']): r for r in rows if r['arm'] == reference}
    by_world = {}
    for r in rows:
        if r['arm'] == arm and (r['world'], r['solver_seed']) in base:
            by_world.setdefault(r['world'], []).append(restricted(r, cap) - restricted(base[(r['world'], r['solver_seed'])], cap))
    diffs = [d for values in by_world.values() for d in values]
    if not diffs:
        return {'pairs': 0}
    worlds = sorted(by_world)
    rng = random.Random(20261005)
    means = []
    for _ in range(resamples):
        sample = [d for world in (rng.choice(worlds) for _ in worlds) for d in by_world[world]]
        means.append(sum(sample) / len(sample))
    means.sort()
    return {'pairs': len(diffs), 'worlds': len(worlds),
            'mean_restricted_difference_seconds': round(sum(diffs) / len(diffs), 1),
            'bootstrap95_worlds': [round(means[int(0.025 * resamples)], 1), round(means[int(0.975 * resamples) - 1], 1)],
            'faster_pairs': sum(d < -1e-9 for d in diffs), 'slower_pairs': sum(d > 1e-9 for d in diffs),
            'note': 'negative = faster than the reference arm; same world and solver seed'}


def report(args):
    rows = [json.loads(line) for line in Path(args.runs).read_text(encoding='utf-8').splitlines() if line.strip()]
    arms = sorted({row['arm'] for row in rows}, key=lambda a: (DEFAULT_ARMS + tuple(ARMS)).index(a) if a in ARMS else 99)
    kinds = [kind for kind, _ in MIX if any(row['kind'] == kind for row in rows)]
    references = [name for name in args.reference.split(',') if name]
    out = {'schema': 'spire-final01-sim-study/v2', 'scope': 'synthetic campaign worlds driving the real planner; not native evidence',
           'references': references, 'caps': {}, 'per_class': {}, 'paired': {}, 'paired_per_class': {}}
    for minutes in (30, 45):
        cap = minutes * 60.0
        out['caps'][str(minutes)] = {arm: metrics([r for r in rows if r['arm'] == arm], cap) for arm in arms}
        out['per_class'][str(minutes)] = {kind: {arm: metrics([r for r in rows if r['arm'] == arm and r['kind'] == kind], cap)
                                                 for arm in arms} for kind in kinds}
        out['paired'][str(minutes)] = {reference: {arm: paired_block(rows, arm, reference, cap) for arm in arms
                                                   if arm != reference} for reference in references}
        out['paired_per_class'][str(minutes)] = {
            reference: {kind: {arm: paired_block([r for r in rows if r['kind'] == kind], arm, reference, cap, 1000)
                               for arm in arms if arm != reference} for kind in kinds} for reference in references}
    Path(args.out).write_text(json.dumps(out, indent=1, sort_keys=True) + '\n', encoding='utf-8')
    for minutes in ('30', '45'):
        print('cap', minutes, 'min')
        for arm in arms:
            m = out['caps'][minutes][arm]
            print('  %-18s solved %3d/%3d  RMST %7.1f  late %.3f  median %s  errors %d' % (
                arm, m['solved'], m['runs'], m['restricted_mean_seconds'], m['late_half_area'], m['median_seconds'], m['errors']))
        for kind in kinds:
            line = '   %-11s' % kind + ''.join('  %s %2d/%2d %6.0f' % (arm[:10], out['per_class'][minutes][kind][arm]['solved'],
                                                                     out['per_class'][minutes][kind][arm]['runs'],
                                                                     out['per_class'][minutes][kind][arm]['restricted_mean_seconds'] or 0)
                                               for arm in arms if out['per_class'][minutes][kind][arm]['runs'])
            print(line)
        for reference, block in out['paired'][minutes].items():
            for arm, row in block.items():
                print('   paired vs %-8s %-18s mean %7s  95%% %-16s faster %3s slower %3s' % (
                    reference, arm, row.get('mean_restricted_difference_seconds'), row.get('bootstrap95_worlds'),
                    row.get('faster_pairs'), row.get('slower_pairs')))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    r = sub.add_parser('run')
    r.add_argument('--panel', choices=sorted(PANELS), default='dev')
    r.add_argument('--count', type=int)
    r.add_argument('--arms', type=lambda v: v.split(','), default=list(DEFAULT_ARMS))
    r.add_argument('--minutes', type=int, default=45)
    r.add_argument('--solver-seeds', type=int, default=2)
    r.add_argument('--processes', type=int, default=4)
    r.add_argument('--out', required=True)
    p = sub.add_parser('report')
    p.add_argument('--runs', required=True)
    p.add_argument('--reference', default='i082,i054a,final01', help='comma-separated reference arms')
    p.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    for arm in getattr(args, 'arms', []) or []:
        if arm not in ARMS:
            parser.error('unknown arm: ' + arm)
    run(args) if args.command == 'run' else report(args)


if __name__ == '__main__':
    main()
