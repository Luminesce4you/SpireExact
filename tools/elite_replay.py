"""Offline check of the deck-family cap (iteration-044, part B): who would the focus elites be?

Feeds a retained run's evaluations, in absorption order, into FocusScheduler.add with cluster cap 0
and 2 and looks at the eight elites every 100 evaluations: how many deck families they span and
which share of the rank-weighted focus the largest family holds. The evaluation stream itself was
produced by the old scheduler, so this shows the selection rule, not an outcome.
"""
from pathlib import Path
from collections import Counter
import argparse, ctypes, gzip, json, sys

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
from spire_exact.planning.focus import FocusScheduler


def alike(a, b, percent=75):
    union = sum((a | b).values())
    return union == 0 or 100 * sum((a & b).values()) >= percent * union


def structure(elites, decks):
    """(families among the elites, rank-weighted focus share of the largest family)."""
    families = []
    for index, source in enumerate(elites):
        families.append(next((families[i] for i in range(index) if alike(decks[source.label], decks[elites[i].label])), index))
    weight = Counter()
    for rank, family in enumerate(families):
        weight[family] += 1.0 / (rank + 1)
    total = sum(weight.values())
    return len(set(families)), (max(weight.values()) / total if total else 0.0)


def main():
    p = argparse.ArgumentParser(); p.add_argument('--seed-dir', type=Path, required=True); p.add_argument('--name', required=True)
    p.add_argument('--out', type=Path, required=True); p.add_argument('--cpu-mask', default='0x30000000'); p.add_argument('--stride', type=int, default=100)
    a = p.parse_args()
    k = ctypes.WinDLL('kernel32'); k.GetCurrentProcess.restype = ctypes.c_void_p; k.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    k.SetProcessAffinityMask(k.GetCurrentProcess(), int(a.cpu_mask, 16))
    rows = []
    for line in (a.seed_dir / 'evaluations.jsonl').open(encoding='utf-8'):
        try: rows.append(json.loads(line))
        except ValueError: break
    schedulers = {cap: FocusScheduler(elites=8, pool=48, cluster_cap=cap) for cap in (0, 1, 2, 3)}
    decks = {}
    timeline = []
    count = 0
    for row in rows:
        if row.get('classification') != 'NATIVE_ROUTE_DEATH' or row.get('cache_hit'): continue
        try:
            with gzip.open(a.seed_dir / row['label'] / 'data' / 'decision.json.gz', 'rt', encoding='utf-8') as fh: decision = json.load(fh)
        except OSError: continue
        decks[row['label']] = Counter(c['id'] for c in (decision.get('observation') or {}).get('deck') or [])
        for scheduler in schedulers.values():
            scheduler.add(decision, row['label'])
        count += 1
        if count % a.stride == 0:
            point = {'evaluations': count, 't': row.get('completed_wall_seconds')}
            for cap, scheduler in schedulers.items():
                elites = scheduler._elite_sources()
                families, share = structure(elites, decks)
                floors = [int(source.death_floor) for source in elites]
                point[str(cap)] = {'families': families, 'largest_family_focus_share': round(share, 3), 'elites': len(elites),
                                   'death_floors': floors, 'crowded_out': scheduler.crowded_out}
            timeline.append(point)
    report = {'run': a.name, 'deaths_fed': count, 'timeline': timeline}
    a.out.write_text(json.dumps(report, indent=1), encoding='utf-8')
    print('%s: %d deaths fed' % (a.name, count))
    print('  evals    t(s)   cap0 families / share      cap1                 cap2                 cap3              cap2 elite death floors')
    for point in timeline:
        cells = ['%d / %.0f%%' % (point[str(cap)]['families'], 100 * point[str(cap)]['largest_family_focus_share']) for cap in (0, 1, 2, 3)]
        print('  %5d %7.0f   %-22s %-20s %-20s %-17s %s' % (point['evaluations'], point['t'] or 0, cells[0], cells[1], cells[2], cells[3], point['2']['death_floors']))
    for cap in (0, 1, 2, 3):
        tail = [pt[str(cap)] for pt in timeline if pt['evaluations'] > 200]
        if tail:
            print('  cap %d after the first 200: mean families %.2f, mean largest-family share %.0f%%' % (
                cap, sum(x['families'] for x in tail) / len(tail), 100 * sum(x['largest_family_focus_share'] for x in tail) / len(tail)))


if __name__ == '__main__':
    main()
