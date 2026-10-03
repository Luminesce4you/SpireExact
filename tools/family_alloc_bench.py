"""Offline check of the deck-family record as an allocation signal (iteration-051; no native execution).

Replays retained runs in absorption order from the event caches of tools/gatemodel_nn_bench.py.

  entries  every new boss entry: the record its deck family had at that gate before it (entries with an alike
           deck, card-id multiset Jaccard >= 75 %: how many, how many passed, their mean and best outcome) against
           whether the new entry passed. Signals: rate = (1 + passed) / (2 + entries), best, mean, count.
  options  every deviation from a parent trajectory: the evidence the gate table had about the option's items
           (novelty = sqrt(ridge / (centred sum of squares + ridge)), 1 = never seen) against whether the child
           did better than its parent at the gate.

    python tools/family_alloc_bench.py --out report.json <cache.pkl | run/seed-<seed>> ...

Outcome scale as in the gate model. Allocation-signal check only: nothing here is a bound or a claim about any state.
"""
from pathlib import Path
import argparse, ctypes, json, math, pickle, sys

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'tools'))
import numpy as np

PERCENT, RIDGE, FITTED, HISTORY, CLASS = 75, 3.0, 24, 8, 10
SIZES = ((0, 0), (1, 7), (8, 29), (30, 99), (100, 10 ** 9))
SIGNALS = ('rate', 'best', 'mean', 'count')
NOVELTY = (('novel >= 0.50', 0.5, 2.0), ('0.25 - 0.50', 0.25, 0.5), ('known < 0.25', -1.0, 0.25))


class Gate:
    def __init__(self):
        self.index = {}; self.cards = {}; self.decks = np.zeros((64, 64), np.int16); self.removed = []; self.kept = []
        self.total = 0.0; self.sums = {}; self.squares = {}

    def value(self, i):
        if self.removed[i] is None: return self.kept[i]
        return min(1.0, self.removed[i] / self.total) if self.total > 0 else 0.0

    def vector(self, features):
        for key in features:
            if key.startswith('card:') and key not in self.cards:
                self.cards[key] = len(self.cards)
        if len(self.cards) > self.decks.shape[1]:
            self.decks = np.pad(self.decks, ((0, 0), (0, len(self.cards))))
        x = np.zeros(self.decks.shape[1], np.int16)
        for key, count in features.items():
            if key.startswith('card:'): x[self.cards[key]] = count
        return x

    def family(self, x):
        n = len(self.removed)
        if not n: return []
        shared = np.minimum(self.decks[:n], x).sum(1); union = np.maximum(self.decks[:n], x).sum(1)
        return np.flatnonzero((union == 0) | (100 * shared >= PERCENT * union)).tolist()

    def add(self, key, features, lost, outcome):
        """Mirror of GateModels.add for one fight; returns the entry-level record of a new entry."""
        removed = None
        if lost is not None:
            removed = lost[0]; self.total = max(self.total, lost[1])
        i = self.index.get(key)
        if i is not None:
            if (removed is None and (self.removed[i] is not None or outcome > self.kept[i])) or \
                    (removed is not None and self.removed[i] is not None and removed > self.removed[i]):
                self.removed[i], self.kept[i] = removed, outcome
            return None
        x = self.vector(features); members = self.family(x); n = len(self.removed)
        values = [self.value(j) for j in members]; everyone = [self.value(j) for j in range(n)]
        passed = sum(self.removed[j] is None for j in members)
        centre = sum(everyone) / n if n else 0.5
        record = {'entries': len(members), 'passed': passed, 'rate': (1 + passed) / (2 + len(members)),
                  'best': max(values, default=centre), 'mean': (sum(values) + centre) / (len(values) + 1), 'count': len(members),
                  'pass': removed is None, 'at': n}
        if n == len(self.decks): self.decks = np.pad(self.decks, ((0, n), (0, 0)))
        self.decks[n] = x; self.index[key] = n; self.removed.append(removed); self.kept.append(outcome)
        for name, count in features.items():
            self.sums[name] = self.sums.get(name, 0.0) + count; self.squares[name] = self.squares.get(name, 0.0) + count * count
        return record

    def novelty(self, key):
        n = len(self.removed); total = self.sums.get(key, 0.0)
        return math.sqrt(RIDGE / (max(0.0, self.squares.get(key, 0.0) - total * total / n) + RIDGE))

    def scaled(self, lost, outcome):
        if lost is None: return outcome
        total = max(self.total, lost[1])
        return min(1.0, lost[0] / total) if total > 0 else 0.0


def auc(scores, labels):
    order = sorted(range(len(scores)), key=scores.__getitem__); ranks = [0.0] * len(scores); i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]: j += 1
        for k in range(i, j + 1): ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    positive = sum(labels); negative = len(labels) - positive
    if not positive or not negative: return None
    return (sum(r for r, y in zip(ranks, labels) if y) - positive * (positive + 1) / 2) / (positive * negative)


def replay(data, name):
    gates = {}; entries = {}; options = []
    for event in data['events']:
        choice = event['choice']
        if choice is not None:
            gate = gates.get(tuple(choice['gate']))
            if gate is not None and len(gate.removed) >= FITTED:
                seen = [sum(gate.novelty(key) for key in delta if not key.startswith('#')) / max(1, sum(not key.startswith('#') for key in delta))
                        for delta in choice['deltas'] if delta]
                if seen:
                    child, parent = gate.scaled(*choice['child']), gate.scaled(*choice['parent'])
                    options.append({'gate': list(choice['gate']), 'novelty': max(seen), 'value': choice['prod'], 'better': child > parent,
                                    'worse': child < parent, 'change': child - parent, 'rescued': choice['child'][0] is None and choice['parent'][0] is not None,
                                    'parent_lost': choice['parent'][0] is not None})
        for gate_id, key, features, lost, outcome in event['adds']:
            gate = gates.setdefault(tuple(gate_id), Gate())
            record = gate.add(key, features, lost, outcome)
            if record is not None: entries.setdefault(tuple(gate_id), []).append(record)
    report = {'run': name, 'gates': [], 'options': options}
    for gate_id, records in sorted(entries.items()):
        scored = [r for r in records if r['entries'] >= HISTORY]
        labels = [r['pass'] for r in scored]
        row = {'gate': list(gate_id), 'entries': len(records), 'passed': sum(r['pass'] for r in records), 'scored': len(scored), 'scored_passed': sum(labels),
               'sizes': [[sum(lo <= r['entries'] <= hi for r in records), sum(r['pass'] for r in records if lo <= r['entries'] <= hi)] for lo, hi in SIZES],
               'dry': [[sum(r['entries'] >= m and r['passed'] == 0 for r in records), sum(r['pass'] for r in records if r['entries'] >= m and r['passed'] == 0)]
                       for m in (8, 30)],
               'auc': None}
        if min(sum(labels), len(labels) - sum(labels)) >= CLASS:
            row['auc'] = {signal: auc([r[signal] for r in scored], labels) for signal in SIGNALS}
        report['gates'].append(row)
    return report


def show(report):
    print('\n%s' % report['run'])
    print('  gate   entries passed | by family size before the entry: new / 1-7 / 8-29 / 30-99 / 100+ (entries: pass %%) | dry >= 8, >= 30 (entries: passed) | AUC rate / best / mean / count')
    for row in report['gates']:
        sizes = ' '.join('%4d:%3.0f%%' % (n, 100 * s / n) if n else '   -     ' for n, s in row['sizes'])
        dry = ' '.join('%4d:%-3d' % (n, s) for n, s in row['dry'])
        cells = ' / '.join('%.2f' % row['auc'][s] for s in SIGNALS) if row['auc'] else '(too few of one class among %d scored)' % row['scored']
        print('  %-6s %6d %6d | %s | %s | %s' % ('%d.%d' % tuple(row['gate']), row['entries'], row['passed'], sizes, dry, cells))


def groups(options, undecided=True):
    out = []
    for label, lo, hi in NOVELTY:
        rows = [o for o in options if lo <= o['novelty'] < hi and (not undecided or abs(o['value']) < 2.0)]
        lost = [o for o in rows if o['parent_lost']]
        out.append({'group': label, 'options': len(rows), 'better': sum(o['better'] for o in rows), 'worse': sum(o['worse'] for o in rows),
                    'mean_change': sum(o['change'] for o in rows) / len(rows) if rows else None,
                    'parent_lost': len(lost), 'rescued': sum(o['rescued'] for o in lost)})
    return out


def summarise(reports):
    units = [(report['run'], row) for report in reports for row in report['gates'] if row['auc']]
    summary = {'units': len(units), 'mean_auc': {s: sum(row['auc'][s] for _, row in units) / len(units) for s in SIGNALS} if units else {},
               'rate_not_below_best_minus_0.02': sum(row['auc']['rate'] >= row['auc']['best'] - 0.02 for _, row in units),
               'by_gate': {}, 'sizes': [[0, 0] for _ in SIZES], 'dry': [[0, 0], [0, 0]]}
    for gate in sorted({tuple(row['gate']) for _, row in units}):
        rows = [row for _, row in units if tuple(row['gate']) == gate]
        summary['by_gate']['%d.%d' % gate] = {'units': len(rows), **{s: sum(row['auc'][s] for row in rows) / len(rows) for s in SIGNALS}}
    for report in reports:
        for row in report['gates']:
            for total, (n, s) in zip(summary['sizes'], row['sizes']): total[0] += n; total[1] += s
            for total, (n, s) in zip(summary['dry'], row['dry']): total[0] += n; total[1] += s
    pooled = [o for report in reports for o in report['options']]
    summary['options_undecided'] = groups(pooled); summary['options_all'] = groups(pooled, False)
    agree = usable = 0
    for report in reports:
        g = groups(report['options'])
        if g[0]['options'] >= 30 and g[2]['options'] >= 30:
            usable += 1; agree += g[0]['better'] / g[0]['options'] > g[2]['better'] / g[2]['options']
    summary['runs_novel_better'] = [agree, usable]
    return summary


def main():
    p = argparse.ArgumentParser(); p.add_argument('sources', type=Path, nargs='+'); p.add_argument('--out', type=Path, required=True)
    p.add_argument('--cache-dir', type=Path, default=None); p.add_argument('--cpu-mask', default='0xFFFF0000')
    a = p.parse_args()
    k = ctypes.WinDLL('kernel32'); k.GetCurrentProcess.restype = ctypes.c_void_p; k.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    k.SetProcessAffinityMask(k.GetCurrentProcess(), int(a.cpu_mask, 16))
    reports = []
    for source in a.sources:
        if source.is_dir():
            import gatemodel_nn_bench as nn
            name = source.parent.name; cache = (a.cache_dir or a.out.parent) / ('cache-%s.pkl' % name)
            if cache.is_file(): data = pickle.loads(cache.read_bytes())
            else:
                data = nn.extract(source); cache.parent.mkdir(parents=True, exist_ok=True); cache.write_bytes(pickle.dumps(data))
        else:
            name = source.stem.replace('cache-', ''); data = pickle.loads(source.read_bytes())
        reports.append(replay(data, name)); show(reports[-1])
    summary = summarise(reports)
    print('\n%d gates with at least %d passed and %d lost entries among those whose family already had %d entries' % (summary['units'], CLASS, CLASS, HISTORY))
    if summary['units']:
        print('  mean AUC for "the next entry passes": ' + ', '.join('%s %.3f' % (s, summary['mean_auc'][s]) for s in SIGNALS))
        print('  rate not more than 0.02 below best in %d / %d gates' % (summary['rate_not_below_best_minus_0.02'], summary['units']))
        for gate, row in summary['by_gate'].items():
            print('    gate %s (%d): ' % (gate, row['units']) + ', '.join('%s %.3f' % (s, row[s]) for s in SIGNALS))
    print('  pass rate by family size before the entry: ' + ', '.join(
        '%s %d / %d (%.1f%%)' % ('new' if hi == 0 else '%d+' % lo if hi > 10 ** 6 else '%d-%d' % (lo, hi), s, n, 100 * s / max(1, n)) for (lo, hi), (n, s) in zip(SIZES, summary['sizes'])))
    print('  entries made in a family with no pass after 8 / 30 entries: ' + ', '.join('%d (%d passed, %.1f%%)' % (n, s, 100 * s / max(1, n)) for n, s in summary['dry']))
    for title, key in (('options where the model had no clear opinion (|z| < 2)', 'options_undecided'), ('all options', 'options_all')):
        print('  ' + title)
        for g in summary[key]:
            if g['options']:
                print('    %-14s %5d options: child better %.1f%%, worse %.1f%%, mean change %+.3f; parent lost %d, child passed %.1f%%' % (
                    g['group'], g['options'], 100 * g['better'] / g['options'], 100 * g['worse'] / g['options'], g['mean_change'],
                    g['parent_lost'], 100 * g['rescued'] / max(1, g['parent_lost'])))
    print('  runs where the novel group beats the known group (both >= 30 options, |z| < 2): %d / %d' % tuple(summary['runs_novel_better']))
    for report in reports: report['options'] = groups(report['options'])
    a.out.write_text(json.dumps({'summary': summary, 'runs': reports}, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
