"""Offline comparison of the gate model's ridge regression with small neural networks (no native execution).

A retained run is replayed in absorption order, the way tools/gatemodel_bench.py does. Every model sees the
same training rows (GateModels' table: one row per distinct boss entry, best outcome seen) and is refitted at
the same moments. Before an evaluation is absorbed each model is asked for

  entry    the outcome of every boss entry the evaluation searched and the table has not seen yet;
  option   f(parent entry + what the option adds) - f(parent entry) for the option the evaluation deviated to,
           compared afterwards with how the outcome at the next boss moved against the parent trajectory.

Models: `prod` (GateModels itself, for reference), `ridge` (the same regression on this tool's schedule),
`mlp32` (one hidden layer, 32 tanh), `mlp64x2` (two hidden layers, 64 ReLU), `ridge+mlp16` (ridge plus a
16 unit tanh network on its residual), `mean` (running mean). The networks are plain numpy, full-batch Adam,
the mean of three fixed initialisations; hyperparameters are fixed in experiments/iteration-048/plan.md.

    python tools/gatemodel_nn_bench.py run --seed-dir <run>/seed-<seed> --cache <file.pkl> --out <report.json>
    python tools/gatemodel_nn_bench.py report --out summary.json <report.json> ...

Predictions are allocation signals. Nothing here is a bound or a claim about any state.
"""
from pathlib import Path
import argparse, ctypes, gzip, hashlib, json, os, pickle, sys, time

for _name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'): os.environ.setdefault(_name, '1')
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'tools'))
from spire_exact.planning.gatemodel import GateModels, boss_fight_rows, entry_features, _ridge, UNSCORED, UNSCORED_HEADS, SIZE_LABELS
from gatemodel_bench import spearman, auc

RIDGE, PASSES, MINIMUM, REFRESH, GROWTH = 3.0, 12, 24, 16, 0.10
STEPS, RATE, SEEDS = 400, 0.01, (0, 1, 2)
NETS = {'mlp32': ((32,), 'tanh'), 'mlp64x2': ((64, 64), 'relu')}
MODELS = ('prod', 'ridge', 'mlp32', 'mlp64x2', 'ridge+mlp16')
SCORED_MINIMUM, EARLY = 60, 150


def label_delta(label, learned):
    """Entry-feature change of taking one option; None when the label carries none (GateModels values it 0)."""
    if label in UNSCORED or label in SIZE_LABELS: return None
    head, _, name = label.partition(':')
    if head in UNSCORED_HEADS: return None
    if head in ('card', 'obtain', 'duplicate'): return {'card:' + name: 1, '#deck': 0.1}
    if head == 'relic': return {label: 1}
    if head == 'upgrade': return {'up:' + name: 1}
    if head == 'remove': return {'card:' + name: -1, '#deck': -0.1}
    if head == 'transform': return {'card:' + name: -1}
    delta = learned.get(label)
    if not delta: return None
    delta = dict(delta); cards = sum(value for key, value in delta.items() if key.startswith('card:'))
    if cards: delta['#deck'] = cards / 10.0
    return delta


def extract(seed_dir: Path, limit: int = 0):
    """One event per absorbed evaluation: the entries it searched, the option it deviated to, the rows it adds."""
    rows = []
    for line in (seed_dir / 'evaluations.jsonl').open(encoding='utf-8'):
        try: rows.append(json.loads(line))
        except ValueError: break
    if limit: rows = rows[:limit]
    model = GateModels(271828); events = []; owner = {}
    for row in rows:
        if row.get('classification') not in ('NATIVE_ROUTE_DEATH', 'NATIVE_WIN_CANDIDATE') or row.get('cache_hit') or row.get('kind') == 'gate_probe':
            continue
        try:
            with gzip.open(seed_dir / row['label'] / 'data' / 'decision.json.gz', 'rt', encoding='utf-8-sig') as fh: decision = json.load(fh)
        except OSError: continue
        prefix = int(row.get('prefix_length') or 0)
        trace = decision.get('trace') or []
        evidence = (decision.get('decision_evidence') or [])[:len(trace)]
        fights = boss_fight_rows(decision)
        mine = {tuple(f['gate']): f for f in fights}
        running = hashlib.blake2b(digest_size=12); digests = [running.digest()]
        for action in trace:
            running.update(json.dumps(action, sort_keys=True, separators=(',', ':')).encode('utf-8')); running.update(b'\n')
            digests.append(running.digest())
        event = {'queries': [], 'adds': [], 'choice': None}
        for fight in fights:
            gate = tuple(fight['gate'])
            item = (gate, digests[fight['index']], entry_features(fight['entry']), fight['lost'], fight['outcome'])
            event['adds'].append(item)
            if fight['index'] >= prefix: event['queries'].append(item + (model.predict(gate, fight['entry']),))
        source = (row.get('repair') or {}).get('source')
        if str(row.get('kind') or '').startswith('macro_') and 0 < prefix <= len(trace) and prefix - 1 < len(evidence):
            site = evidence[prefix - 1]
            act = int((site.get('observation') or {}).get('act') or 0)
            labels = None
            wanted = json.dumps(trace[prefix - 1], sort_keys=True, separators=(',', ':'))
            for action, names in zip(site.get('available_actions') or [], site.get('option_labels') or []):
                if json.dumps(action, sort_keys=True, separators=(',', ':')) == wanted: labels = names; break
            parent = owner.get(digests[prefix - 1])
            gate = (act, 0)
            if parent is not None:
                source = parent[0]
                if labels and gate in mine and gate in parent[1] and mine[gate]['index'] >= prefix:
                    event['choice'] = {'gate': gate, 'labels': labels, 'deltas': [label_delta(label, model.deltas) for label in labels],
                                       'prod': model.best(labels, act), 'parent_x': entry_features(parent[1][gate]['entry']),
                                       'child': (mine[gate]['lost'], mine[gate]['outcome']),
                                       'parent': (parent[1][gate]['lost'], parent[1][gate]['outcome']), 'parent_label': parent[0]}
        for index in range(len(trace) + 1):
            owner.setdefault(digests[index], (row['label'], mine))
        model.add(decision, row['label'], source)
        events.append(event)
    return {'seed_dir': str(seed_dir), 'evaluations': len(rows), 'events': events}


def train(X, target, hidden, activation, penalty):
    """Full-batch Adam on mean squared error + penalty * sum of squared weights; one parameter set per seed."""
    n, d = X.shape; sizes = [d, *hidden, 1]; nets = []
    target = target.astype(np.float32).reshape(-1, 1)
    for seed in SEEDS:
        rng = np.random.default_rng(1000 + seed)
        gain = 2.0 if activation == 'relu' else 1.0
        W = [(rng.standard_normal((a, b)) * np.sqrt(gain / a)).astype(np.float32) for a, b in zip(sizes, sizes[1:])]
        B = [np.zeros(b, np.float32) for b in sizes[1:]]
        params = W + B; m = [np.zeros_like(p) for p in params]; v = [np.zeros_like(p) for p in params]
        for step in range(1, STEPS + 1):
            hs = [X]
            for w, b in zip(W[:-1], B[:-1]):
                z = hs[-1] @ w + b
                hs.append(np.tanh(z) if activation == 'tanh' else np.maximum(z, 0))
            g = (2.0 / n) * (hs[-1] @ W[-1] + B[-1] - target)
            grads_w, grads_b = [None] * len(W), [None] * len(B)
            for layer in range(len(W) - 1, -1, -1):
                grads_w[layer] = hs[layer].T @ g + 2 * penalty * W[layer]; grads_b[layer] = g.sum(0)
                if layer:
                    g = g @ W[layer].T
                    g = g * (1 - hs[layer] ** 2) if activation == 'tanh' else g * (hs[layer] > 0)
            scale = RATE * np.sqrt(1 - 0.999 ** step) / (1 - 0.9 ** step)
            for i, grad in enumerate(grads_w + grads_b):
                m[i] = 0.9 * m[i] + 0.1 * grad; v[i] = 0.999 * v[i] + 0.001 * grad * grad
                params[i] -= (scale * m[i] / (np.sqrt(v[i]) + 1e-8)).astype(np.float32)
        nets.append((W, B, activation))
    return nets


def forward(nets, X):
    total = 0.0
    for W, B, activation in nets:
        h = X
        for w, b in zip(W[:-1], B[:-1]):
            z = h @ w + b; h = np.tanh(z) if activation == 'tanh' else np.maximum(z, 0)
        total = total + (h @ W[-1] + B[-1])[:, 0]
    return total / len(nets)


class Fit:
    """Every compared model fitted on one snapshot of a gate's table."""
    def __init__(self, rows, warm):
        n = len(rows)
        self.keys = sorted({key for features, _ in rows for key in features}); self.index = {key: i for i, key in enumerate(self.keys)}
        X = self.matrix([features for features, _ in rows]); y = np.array([value for _, value in rows], np.float64)
        self.mu = X.mean(0); centred = X - self.mu
        self.intercept, self.weights, _, _ = _ridge(rows, RIDGE, PASSES, warm)
        self.w = np.array([self.weights.get(key, 0.0) for key in self.keys], np.float64)
        self.ymean, self.ysd = float(y.mean()), max(float(y.std()), 1e-6)
        penalty = RIDGE / n
        self.nets = {name: train(centred, (y - self.ymean) / self.ysd, hidden, activation, penalty) for name, (hidden, activation) in NETS.items()}
        residual = y - (self.intercept + X @ self.w)
        self.rmean, self.rsd = float(residual.mean()), max(float(residual.std()), 1e-6)
        self.residual_net = train(centred, (residual - self.rmean) / self.rsd, (16,), 'tanh', penalty)

    def matrix(self, features_list):
        X = np.zeros((len(features_list), len(self.keys)), np.float32)
        for i, features in enumerate(features_list):
            for key, value in features.items():
                j = self.index.get(key)
                if j is not None: X[i, j] = value
        return X

    def predict(self, features_list):
        X = self.matrix(features_list); centred = X - self.mu
        linear = self.intercept + X.astype(np.float64) @ self.w
        out = {'mean': np.full(len(features_list), self.ymean), 'ridge': linear}
        for name, nets in self.nets.items(): out[name] = self.ymean + self.ysd * forward(nets, centred)
        out['ridge+mlp16'] = linear + self.rmean + self.rsd * forward(self.residual_net, centred)
        return out


class Table:
    """A gate's training table, kept the way GateModels.add keeps it."""
    def __init__(self):
        self.entries = {}; self.total = 0.0; self.dirty = 0; self.fit = None; self.fitted = 0; self.fits = 0

    def add(self, key, features, lost, outcome):
        removed = None
        if lost is not None:
            removed = lost[0]
            if lost[1] > self.total: self.total = lost[1]; self.dirty += 1
            outcome = min(1.0, removed / self.total) if self.total > 0 else 0.0
        row = self.entries.get(key)
        if row is None:
            self.entries[key] = [features, outcome, removed]; self.dirty += 1
        elif (removed is None and (row[2] is not None or outcome > row[1])) or (removed is not None and row[2] is not None and removed > row[2]):
            row[1], row[2] = outcome, removed; self.dirty += 1
        if len(self.entries) >= MINIMUM and self.dirty >= max(REFRESH, GROWTH * self.fitted):
            for row in self.entries.values():
                if row[2] is not None: row[1] = min(1.0, row[2] / self.total) if self.total > 0 else 0.0
            rows = [(row[0], row[1]) for row in self.entries.values()]
            self.fit = Fit(rows, self.fit.weights if self.fit else None); self.fitted = len(rows); self.dirty = 0; self.fits += 1


def compare(data):
    tables = {}; entries = {}; choices = []
    for event in data['events']:
        for gate, key, features, lost, outcome, prod in event['queries']:
            table = tables.get(gate)
            if table is not None and key in table.entries: continue        # a gate retry of an entry the table already has
            record = {'lost': lost, 'outcome': outcome, 'hp': features.get('#hp', 0.0), 'pred': None}
            if table is not None and table.fit is not None and prod is not None:
                record['pred'] = {name: float(values[0]) for name, values in table.fit.predict([features]).items()}; record['pred']['prod'] = prod
            entries.setdefault(gate, []).append(record)
        choice = event['choice']
        if choice is not None:
            table = tables.get(choice['gate']); value = {'prod': choice['prod']}
            deltas = [delta for delta in choice['deltas'] if delta]
            if table is not None and table.fit is not None and deltas:
                base = choice['parent_x']; variants = [base]
                for delta in deltas:
                    moved = dict(base)
                    for key, change in delta.items(): moved[key] = moved.get(key, 0) + change
                    variants.append(moved)
                unscored = len(deltas) < len(choice['deltas'])                # GateModels.best includes the 0 of an unvalued label
                for name, values in table.fit.predict(variants).items():
                    gains = [float(v - values[0]) for v in values[1:]] + ([0.0] if unscored else [])
                    value[name] = max(gains)
            choices.append({'gate': choice['gate'], 'value': value, 'child': choice['child'], 'parent': choice['parent'], 'parent_label': choice['parent_label']})
        for gate, key, features, lost, outcome in event['adds']:
            tables.setdefault(gate, Table()).add(key, features, lost, outcome)
    report = {'seed_dir': data['seed_dir'], 'evaluations': data['evaluations'], 'gates': {}}
    for gate in sorted(entries):
        mine = entries[gate]; table = tables[gate]; total = max([e['lost'][1] for e in mine if e['lost']] + [0.0])
        def y(lost, outcome): return outcome if lost is None else (min(1.0, lost[0] / total) if total else 0.0)
        for e in mine: e['y'] = y(e['lost'], e['outcome'])
        known = [e for e in mine if e['pred'] is not None]; truth = [e['y'] for e in known]
        info = {'distinct_entries': len(table.entries), 'scored': len(mine), 'with_prediction': len(known), 'passes': sum(v >= 1 for v in truth), 'fits': table.fits,
                'hp_only_spearman': spearman([e['hp'] for e in known], truth), 'models': {}}
        base_error = sum((e['pred']['mean'] - e['y']) ** 2 for e in known) or 1.0
        for name in MODELS + ('mean',):
            scores = [e['pred'][name] for e in known]
            info['models'][name] = {'spearman': spearman(scores, truth), 'early_spearman': spearman(scores[:EARLY], truth[:EARLY]),
                                    'pass_auc': auc(scores, [v >= 1 for v in truth]),
                                    'skill': 1.0 - sum((s - t) ** 2 for s, t in zip(scores, truth)) / base_error if known else None}
        # What no model of these features can explain: outcome variance among entries with identical features.
        groups = {}
        for features, outcome, _ in table.entries.values(): groups.setdefault(tuple(sorted(features.items())), []).append(outcome)
        values = [v for group in groups.values() for v in group]; centre = sum(values) / len(values)
        within = sum((v - sum(group) / len(group)) ** 2 for group in groups.values() for v in group)
        info['identical_feature_entries'] = sum(len(group) for group in groups.values() if len(group) > 1)
        info['within_identical_variance_share'] = within / (sum((v - centre) ** 2 for v in values) or 1.0)
        picks = [c for c in choices if c['gate'] == gate]
        for c in picks: c['change'] = y(*c['child']) - y(*c['parent'])
        by_parent = {}
        for c in picks: by_parent.setdefault(c['parent_label'], []).append(c)
        pairs = [(a, b) for group in by_parent.values() for i, a in enumerate(group) for b in group[i + 1:] if a['change'] != b['change']]
        info['options'] = len(picks); info['same_parent_pairs'] = len(pairs)
        for name in MODELS:
            own = [0, 0]; shared = [0, 0]
            for a, b in pairs:
                dv = a['value'].get(name, 0.0) - b['value'].get(name, 0.0)
                if not dv: continue
                right = (dv > 0) == (a['change'] > b['change'])
                own[0] += right; own[1] += 1
                if a['value']['prod'] != b['value']['prod']: shared[0] += right; shared[1] += 1
            valued = [c for c in picks if c['value'].get(name, 0.0) != 0.0]
            info['models'][name].update(pairs_right=own, pairs_right_where_prod_orders=shared,
                                        option_spearman=spearman([c['value'][name] for c in valued], [c['change'] for c in valued]))
        report['gates'][str(gate)] = info
    return report


def show(report):
    fmt = lambda v: ' n/a ' if v is None else format(v, '+.3f')
    print('%s: %d evaluations' % (report['seed_dir'], report['evaluations']))
    for gate, info in report['gates'].items():
        print('  gate %s: %d distinct entries, %d scored with a prediction (%d pass), %d fits; identical-feature entries %d, variance share within them %.2f; HP only %s' % (
            gate, info['distinct_entries'], info['with_prediction'], info['passes'], info['fits'], info['identical_feature_entries'],
            info['within_identical_variance_share'], fmt(info['hp_only_spearman'])))
        for name in MODELS + ('mean',):
            m = info['models'][name]; line = '    %-12s Spearman %s (first %d %s)  AUC %s  skill %s' % (name, fmt(m['spearman']), EARLY, fmt(m['early_spearman']), fmt(m['pass_auc']), fmt(m['skill']))
            if 'pairs_right' in m:
                a, b = m['pairs_right'], m['pairs_right_where_prod_orders']
                line += '  | pairs right %d / %d (%s), where prod orders %d / %d (%s), option Spearman %s' % (
                    a[0], a[1], '%.0f%%' % (100 * a[0] / a[1]) if a[1] else 'n/a', b[0], b[1], '%.0f%%' % (100 * b[0] / b[1]) if b[1] else 'n/a', fmt(m['option_spearman']))
            print(line)


def summarise(reports):
    """The comparison fixed in the plan: per gate Spearman against `ridge`, pooled same-parent pair agreement."""
    units = [(r['seed_dir'], gate, info) for r in reports for gate, info in r['gates'].items() if info['with_prediction'] >= SCORED_MINIMUM and info['models']['ridge']['spearman'] is not None]
    out = {'units': len(units), 'scored_minimum': SCORED_MINIMUM, 'models': {}}
    for name in MODELS:
        diffs = [info['models'][name]['spearman'] - info['models']['ridge']['spearman'] for _, _, info in units if info['models'][name]['spearman'] is not None]
        aucs = [(info['models'][name]['pass_auc'], info['models']['ridge']['pass_auc']) for _, _, info in units]
        aucs = [a - b for a, b in aucs if a is not None and b is not None]
        skills = [info['models'][name]['skill'] - info['models']['ridge']['skill'] for _, _, info in units]
        early = [(info['models'][name]['early_spearman'], info['models']['ridge']['early_spearman']) for _, _, info in units]
        early = [a - b for a, b in early if a is not None and b is not None]
        own = [sum(info['models'][name]['pairs_right'][i] for r in reports for info in r['gates'].values()) for i in (0, 1)]
        shared = [sum(info['models'][name]['pairs_right_where_prod_orders'][i] for r in reports for info in r['gates'].values()) for i in (0, 1)]
        out['models'][name] = {'mean_spearman': sum(info['models'][name]['spearman'] or 0.0 for _, _, info in units) / max(1, len(units)),
                               'mean_spearman_minus_ridge': sum(diffs) / max(1, len(diffs)), 'gates_higher_than_ridge': [sum(d > 0 for d in diffs), len(diffs)],
                               'mean_auc_minus_ridge': sum(aucs) / max(1, len(aucs)), 'gates_auc_higher': [sum(d > 0 for d in aucs), len(aucs)],
                               'mean_skill_minus_ridge': sum(skills) / max(1, len(skills)),
                               'mean_early_spearman_minus_ridge': sum(early) / max(1, len(early)),
                               'pairs_right': own, 'pairs_right_where_prod_orders': shared}
    return out


def main():
    p = argparse.ArgumentParser(); sub = p.add_subparsers(dest='command', required=True)
    r = sub.add_parser('run'); r.add_argument('--seed-dir', type=Path, required=True); r.add_argument('--cache', type=Path, required=True)
    r.add_argument('--out', type=Path, required=True); r.add_argument('--limit', type=int, default=0); r.add_argument('--cpu-mask', default='0xFFFF0000')
    s = sub.add_parser('report'); s.add_argument('reports', type=Path, nargs='+'); s.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if a.command == 'report':
        reports = [json.loads(path.read_text(encoding='utf-8')) for path in a.reports]
        for report in reports: show(report)
        summary = summarise(reports)
        print('\n%d gates with at least %d scored entries' % (summary['units'], SCORED_MINIMUM))
        for name, m in summary['models'].items():
            a_, b_ = m['pairs_right'], m['pairs_right_where_prod_orders']
            print('  %-12s mean Spearman %+.3f (minus ridge %+.3f, higher in %d / %d; first %d entries %+.3f)  AUC minus ridge %+.3f (higher in %d / %d)  skill minus ridge %+.3f  | pairs right %d / %d (%.1f%%), where prod orders %d / %d (%.1f%%)' % (
                name, m['mean_spearman'], m['mean_spearman_minus_ridge'], *m['gates_higher_than_ridge'], EARLY, m['mean_early_spearman_minus_ridge'],
                m['mean_auc_minus_ridge'], *m['gates_auc_higher'], m['mean_skill_minus_ridge'],
                a_[0], a_[1], 100 * a_[0] / max(1, a_[1]), b_[0], b_[1], 100 * b_[0] / max(1, b_[1])))
        a.out.write_text(json.dumps(summary, indent=1), encoding='utf-8')
        return
    k = ctypes.WinDLL('kernel32'); k.GetCurrentProcess.restype = ctypes.c_void_p; k.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    k.SetProcessAffinityMask(k.GetCurrentProcess(), int(a.cpu_mask, 16))
    started = time.time()
    if a.cache.is_file() and not a.limit:
        data = pickle.loads(a.cache.read_bytes())
    else:
        data = extract(a.seed_dir, a.limit)
        if not a.limit: a.cache.parent.mkdir(parents=True, exist_ok=True); a.cache.write_bytes(pickle.dumps(data))
    extracted = time.time()
    report = compare(data); report['seconds'] = {'extract': round(extracted - started, 1), 'compare': round(time.time() - extracted, 1)}
    show(report)
    a.out.write_text(json.dumps(report, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
