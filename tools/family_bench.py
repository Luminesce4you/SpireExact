"""Deck families at the boss gates of finished runs (read-only): is a family's record a better
allocation signal than its best single fight?

    python tools/family_bench.py <run or seed folder> [...] --out families.json

A gate entry is one searched boss fight, distinct by the trace before it. Families are greedy
clusters, in absorption order, of entries with alike decks (card-id multiset Jaccard >= 75 %).
  early signal  a family's first N entries: do their mean, their best, or the first one say how the
                family's later entries fare?
  next gate     does a family's record at one gate say how its descendants fare at the next gate?
  dry spending  entries made in a family after it had M entries without a pass
Outcome scale as in the gate model: lost = enemy HP removed over the largest life total seen at the
gate, passed = 1 + HP fraction kept. Allocation-signal check only: nothing here is a bound or a
claim about any state.
"""
from pathlib import Path
from collections import Counter
import argparse, ctypes, gzip, hashlib, json, statistics, sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from spire_exact.planning.probes import gate_entries

EARLY = ('mean', 'best', 'first')


def alike(x, y, percent):
    union = sum((x | y).values())
    return union == 0 or 100 * sum((x & y).values()) >= percent * union


def ranks(values):
    order = sorted(range(len(values)), key=values.__getitem__); out = [0.0] * len(values); i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]: j += 1
        for k in range(i, j + 1): out[order[k]] = (i + j) / 2
        i = j + 1
    return out


def spearman(x, y):
    if len(x) < 4: return None
    a, b = ranks(x), ranks(y); ma, mb = statistics.mean(a), statistics.mean(b)
    va = sum((p - ma) ** 2 for p in a); vb = sum((q - mb) ** 2 for q in b)
    return round(sum((p - ma) * (q - mb) for p, q in zip(a, b)) / (va * vb) ** 0.5, 3) if va and vb else None


def show(value):
    return ' n/a ' if value is None else '%+.2f' % value


def resolve(folder):
    if (folder / 'evaluations.jsonl').is_file(): return folder
    found = sorted(p.parent for p in folder.glob('*/evaluations.jsonl'))
    if len(found) != 1: raise SystemExit('no single evaluations.jsonl under ' + str(folder))
    return found[0]


def load(seed_dir):
    """Distinct searched boss entries in absorption order; each knows the previous fight of its trajectory."""
    entries = {}; by_gate = {}
    for line in (seed_dir / 'evaluations.jsonl').open(encoding='utf-8'):
        try: row = json.loads(line)
        except ValueError: break
        if row.get('classification') not in ('NATIVE_ROUTE_DEATH', 'NATIVE_WIN_CANDIDATE') or row.get('cache_hit'): continue
        try:
            with gzip.open(seed_dir / row['label'] / 'data' / 'decision.json.gz', 'rt', encoding='utf-8') as fh: d = json.load(fh)
        except OSError: continue
        previous = None
        for e in gate_entries(d):
            key = hashlib.sha1(json.dumps(d['trace'][:e['index']], sort_keys=True).encode('utf-8')).hexdigest()
            if key not in entries and e['index'] >= (row.get('prefix_length') or 0):
                entries[key] = {'gate': tuple(e['gate']), 'outcome': e['outcome'], 'lost': e['lost'], 'parent': previous,
                                'cards': Counter(c['id'] for c in e['entry'].get('deck') or [])}
                by_gate.setdefault(tuple(e['gate']), []).append(entries[key])
            previous = key
    return entries, by_gate


def families(mine, percent):
    total = max([e['lost'][1] for e in mine if e['lost']] + [0.0])
    clusters = []
    for e in mine:
        e['passed'] = e['lost'] is None
        e['y'] = e['outcome'] if e['passed'] else (min(1.0, e['lost'][0] / total) if total else 0.0)
        for index, c in enumerate(clusters):
            if alike(e['cards'], c[0]['cards'], percent): c.append(e); e['family'] = index; break
        else: e['family'] = len(clusters); clusters.append([e])
    return clusters


def early(clusters, first, minimum):
    rows = []
    for c in clusters:
        if len(c) < minimum: continue
        head, tail = c[:first], c[first:]
        rows.append({'size': len(c), 'first': head[0]['y'], 'best': max(e['y'] for e in head), 'mean': statistics.mean(e['y'] for e in head),
                     'later_mean': statistics.mean(e['y'] for e in tail), 'later_pass': sum(e['passed'] for e in tail) / len(tail)})
    return rows


def dry(clusters, limit):
    spent = late = 0
    for c in clusters:
        passes = 0
        for n, e in enumerate(c):
            if n >= limit and not passes: spent += 1; late += e['passed']
            passes += e['passed']
    return spent, late


def standardise(rows, names):
    out = {}
    for name in names:
        values = [r[name] for r in rows]; m = statistics.mean(values); s = statistics.pstdev(values)
        out[name] = [(v - m) / s if s else 0.0 for v in values]
    return out


def main():
    p = argparse.ArgumentParser(); p.add_argument('folders', nargs='+', type=Path); p.add_argument('--out', type=Path, required=True)
    p.add_argument('--percent', type=int, default=75); p.add_argument('--first', type=int, default=8); p.add_argument('--minimum', type=int, default=16)
    p.add_argument('--children', type=int, default=5); p.add_argument('--dry', type=int, default=30)
    p.add_argument('--cpu-mask', type=lambda x: int(x, 0), default=0x80000000)
    a = p.parse_args()
    k = ctypes.windll.kernel32; k.GetCurrentProcess.restype = ctypes.c_void_p; k.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    k.SetProcessAffinityMask(k.GetCurrentProcess(), a.cpu_mask)
    report = {'parameters': {'percent': a.percent, 'first': a.first, 'minimum': a.minimum, 'children': a.children, 'dry': a.dry}, 'runs': []}
    pooled = {name: [] for name in EARLY + ('later_mean', 'later_pass')}; pooled_next = {name: [] for name in ('pass_rate', 'mean', 'margin', 'child_mean')}
    for folder in a.folders:
        seed_dir = resolve(folder); entries, by_gate = load(seed_dir)
        clusters = {gate: families(mine, a.percent) for gate, mine in sorted(by_gate.items())}
        run = {'folder': str(seed_dir), 'gates': []}; report['runs'].append(run)
        print('%s: %d distinct searched boss entries' % (seed_dir, len(entries)))
        kids = {}
        for e in entries.values():
            parent = entries.get(e['parent'])
            if parent is not None: kids.setdefault((parent['gate'], parent['family']), []).append(e)
        for gate, groups in clusters.items():
            mine = by_gate[gate]; sizes = sorted((len(c) for c in groups), reverse=True); spent, late = dry(groups, a.dry)
            rows = early(groups, a.first, a.minimum)
            signal = {target: {name: spearman([r[name] for r in rows], [r[target] for r in rows]) for name in EARLY} for target in ('later_mean', 'later_pass')}
            if len(rows) >= 3:
                z = standardise(rows, pooled)
                for name in pooled: pooled[name] += z[name]
            following = []
            for (parent_gate, family), children in sorted(kids.items()):
                if parent_gate != gate or len(children) < a.children: continue
                c = groups[family]; passes = [e for e in c if e['passed']]
                following.append({'family': family, 'size': len(c), 'pass_rate': len(passes) / len(c), 'mean': statistics.mean(e['y'] for e in c),
                                  'margin': statistics.mean(e['y'] - 1 for e in passes) if passes else 0.0, 'children': len(children),
                                  'child_mean': statistics.mean(e['y'] for e in children), 'child_pass': sum(e['passed'] for e in children) / len(children)})
            onward = {name: spearman([r[name] for r in following], [r['child_mean'] for r in following]) for name in ('pass_rate', 'mean', 'margin')}
            if len(following) >= 3:
                z = standardise(following, pooled_next)
                for name in pooled_next: pooled_next[name] += z[name]
            run['gates'].append({'gate': list(gate), 'entries': len(mine), 'families': len(groups), 'largest': sizes[:5], 'passes': sum(e['passed'] for e in mine),
                                 'dry_entries': spent, 'dry_passes': late, 'early': rows, 'early_spearman': signal, 'next': following, 'next_spearman': onward})
            print('  gate %s: %d entries in %d families (largest %s), %d passes; %d entries made after %d without a pass in the family, %d of them passed' % (
                gate, len(mine), len(groups), sizes[:3], sum(e['passed'] for e in mine), spent, a.dry, late))
            print('    early signal, %d families: with the later mean: mean of %d %s, best of %d %s, first %s;  with the later pass rate: %s / %s / %s' % (
                len(rows), a.first, show(signal['later_mean']['mean']), a.first, show(signal['later_mean']['best']), show(signal['later_mean']['first']),
                show(signal['later_pass']['mean']), show(signal['later_pass']['best']), show(signal['later_pass']['first'])))
            print('    next gate, %d families: the descendants\' mean there with the pass rate here %s, the mean here %s, the HP kept by passes %s' % (
                len(following), show(onward['pass_rate']), show(onward['mean']), show(onward['margin'])))
            for r in sorted(following, key=lambda r: -r['children'])[:6]:
                print('        family of %4d: pass rate %5.1f%%, mean %.3f, HP kept %.3f -> %4d entries at the next gate, mean %.3f, pass rate %5.1f%%' % (
                    r['size'], 100 * r['pass_rate'], r['mean'], r['margin'], r['children'], r['child_mean'], 100 * r['child_pass']))
    report['pooled_early'] = {'families': len(pooled['mean']), **{target: {name: spearman(pooled[name], pooled[target]) for name in EARLY} for target in ('later_mean', 'later_pass')}}
    report['pooled_next'] = {'families': len(pooled_next['mean']), **{name: spearman(pooled_next[name], pooled_next['child_mean']) for name in ('pass_rate', 'mean', 'margin')}}
    e, n = report['pooled_early'], report['pooled_next']
    print('pooled early signal (%d families, standardised within each gate): with the later mean: mean of %d %s, best of %d %s, first %s;  with the later pass rate: %s / %s / %s' % (
        e['families'], a.first, show(e['later_mean']['mean']), a.first, show(e['later_mean']['best']), show(e['later_mean']['first']),
        show(e['later_pass']['mean']), show(e['later_pass']['best']), show(e['later_pass']['first'])))
    print('pooled next gate (%d families): pass rate here %s, mean here %s, HP kept by passes %s' % (n['families'], show(n['pass_rate']), show(n['mean']), show(n['margin'])))
    a.out.write_text(json.dumps(report, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
