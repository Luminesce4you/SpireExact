"""Offline benchmark of the per-seed gate model on retained runs (no native execution).

Replays a run's evaluations in absorption order. Before a result is added, the model as it stood
at that moment is asked two things:

  entry    what it predicts for every boss entry this evaluation had to search (level regression);
  option   what it thinks of the option the evaluation deviated to (the value the focus scheduler
           and the rollout tiers use), compared afterwards with how the entry outcome at the next
           boss moved against the trajectory the evaluation deviated from.

Reported per gate: prequential Spearman of the entry prediction, the pass / fail AUC, and for the
options the Spearman and sign agreement between value and outcome change. Use it to compare model
variants (--options is passed to GateModels) before any native run:

    python tools/gatemodel_bench.py --seed-dir <run>/seed-<seed> --out report.json
    python tools/gatemodel_bench.py --seed-dir ... --options "{\"size\": true}" --out size.json

Predictions are allocation signals. Nothing here is a bound or a claim about any state.
"""
from pathlib import Path
import argparse, ctypes, gzip, hashlib, json, sys

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
from spire_exact.planning.gatemodel import GateModels, boss_fight_rows


def spearman(x, y):
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i]); r = [0.0] * len(v); i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]: j += 1
            for k in range(i, j + 1): r[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return r
    n = len(x)
    if n < 8: return None
    rx, ry = rank(x), rank(y); mx, my = sum(rx) / n, sum(ry) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry)); sxx = sum((a - mx) ** 2 for a in rx); syy = sum((b - my) ** 2 for b in ry)
    return sxy / (sxx * syy) ** .5 if sxx and syy else None


def auc(scores, positives):
    """Probability that a passing entry is ranked above a failing one (ties count half)."""
    pos = [s for s, p in zip(scores, positives) if p]; neg = [s for s, p in zip(scores, positives) if not p]
    if len(pos) < 3 or len(neg) < 3: return None
    wins = sum((a > b) + 0.5 * (a == b) for a in pos for b in neg)
    return wins / (len(pos) * len(neg))


class LevelOnly(GateModels):
    """Benchmark variant: option values from the level regression alone."""
    @staticmethod
    def _item(gate, key): return gate.z.get(key, 0.0)


class PairOnly(GateModels):
    """Benchmark variant: option values from the parent / child difference regression alone."""
    @staticmethod
    def _item(gate, key): return gate.pair_z.get(key, 0.0) if gate.pairs_fitted else 0.0


COMPONENTS = {'mean': GateModels, 'level': LevelOnly, 'pair': PairOnly}


def replay(seed_dir: Path, options: dict, limit: int = 0, component: str = 'mean'):
    rows = []
    for line in (seed_dir / 'evaluations.jsonl').open(encoding='utf-8'):
        try: rows.append(json.loads(line))
        except ValueError: break
    if limit: rows = rows[:limit]
    model = COMPONENTS[component](271828, **options)
    entries = {}                 # gate -> [{'pred', 'removed' or None, 'outcome', 'total_at_time'}]
    choices = []                 # deviations with a known parent outcome at the next boss
    owner = {}                   # digest of an action prefix -> gate outcomes of the first trajectory that had it
    for row in rows:
        if row.get('classification') not in ('NATIVE_ROUTE_DEATH', 'NATIVE_WIN_CANDIDATE') or row.get('cache_hit') or row.get('kind') == 'gate_probe':
            continue
        try:
            with gzip.open(seed_dir / row['label'] / 'data' / 'decision.json.gz', 'rt', encoding='utf-8') as fh: decision = json.load(fh)
        except OSError: continue
        prefix = int(row.get('prefix_length') or 0)
        trace = decision.get('trace') or []
        evidence = (decision.get('decision_evidence') or [])[:len(trace)]
        fights = boss_fight_rows(decision)
        mine = {tuple(f['gate']): f for f in fights}
        for fight in fights:
            if fight['index'] < prefix: continue
            entries.setdefault(tuple(fight['gate']), []).append(
                {'pred': model.predict(tuple(fight['gate']), fight['entry']), 'lost': fight['lost'], 'outcome': fight['outcome'],
                 'hp': float(fight['entry'].get('hp') or 0) / max(1.0, float(fight['entry'].get('max_hp') or 1))})
        # Running digests of every prefix of this trajectory.
        running = hashlib.blake2b(digest_size=12); digests = [running.digest()]
        for action in trace:
            running.update(json.dumps(action, sort_keys=True, separators=(',', ':')).encode('utf-8')); running.update(b'\n')
            digests.append(running.digest())
        # The trajectory this evaluation deviated from (or re-solved): what the search passes to the
        # difference model. For a macro deviation it is the first trajectory that had the prefix.
        source = (row.get('repair') or {}).get('source')
        if row['kind'].startswith('macro_') and 0 < prefix <= len(trace) and prefix - 1 < len(evidence):
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
                    choices.append({'gate': gate, 'value': model.best(labels, act), 'labels': labels, 'phase': site.get('phase'),
                                    'child': mine[gate], 'parent': parent[1][gate], 'parent_label': parent[0]})
        for index in range(len(trace) + 1):
            owner.setdefault(digests[index], (row['label'], mine))
        model.add(decision, row['label'], source)
    return rows, entries, choices, model


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--seed-dir', type=Path, action='append', required=True); p.add_argument('--out', type=Path, required=True)
    p.add_argument('--options', default='{}', help='JSON keyword arguments for GateModels'); p.add_argument('--limit', type=int, default=0)
    p.add_argument('--component', choices=sorted(COMPONENTS), default='mean', help='which regression values the options (mean = the shipped model)')
    p.add_argument('--cpu-mask', default='0x30000000')
    a = p.parse_args()
    k = ctypes.WinDLL('kernel32'); k.GetCurrentProcess.restype = ctypes.c_void_p; k.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    k.SetProcessAffinityMask(k.GetCurrentProcess(), int(a.cpu_mask, 16))
    options = json.loads(a.options)
    report = {'options': options, 'component': a.component, 'runs': []}
    fmt = lambda v: ' n/a ' if v is None else format(v, '+.3f')
    for seed_dir in a.seed_dir:
        rows, entries, choices, model = replay(seed_dir, options, a.limit, a.component)
        run = {'seed_dir': str(seed_dir), 'evaluations': len(rows), 'gates': {}}
        print('%s: %d evaluations' % (seed_dir, len(rows)))
        for gate in sorted(entries):
            mine = entries[gate]
            total = max([e['lost'][1] for e in mine if e['lost']] + [0.0])
            for e in mine:
                e['y'] = e['outcome'] if e['lost'] is None else (min(1.0, e['lost'][0] / total) if total else 0.0)
            known = [e for e in mine if e['pred'] is not None]
            info = {'entries': len(mine), 'passes': sum(e['y'] >= 1 for e in mine), 'with_prediction': len(known),
                    'entry_spearman': spearman([e['pred'] for e in known], [e['y'] for e in known]),
                    'entry_pass_auc': auc([e['pred'] for e in known], [e['y'] >= 1 for e in known]),
                    'hp_only_spearman': spearman([e['hp'] for e in known], [e['y'] for e in known])}
            picks = [c for c in choices if c['gate'] == gate]
            for c in picks:
                def y(f): return f['outcome'] if f['lost'] is None else (min(1.0, f['lost'][0] / total) if total else 0.0)
                c['change'] = y(c['child']) - y(c['parent'])
            valued = [c for c in picks if c['value'] != 0.0]
            agree = sum((c['value'] > 0) == (c['change'] > 0) for c in valued if c['change'] != 0)
            decided = sum(c['change'] != 0 for c in valued)
            # Within one source trajectory the parent is fixed, so the order of its deviations by
            # value can be compared with the order of their outcomes directly (the way the focus
            # scheduler uses the values). Pairs with equal values or equal outcomes are left out.
            by_parent = {}
            for c in picks:
                by_parent.setdefault(c['parent_label'], []).append(c)
            concordant = discordant = 0
            for group in by_parent.values():
                for i, first in enumerate(group):
                    for second in group[i + 1:]:
                        dv, dy = first['value'] - second['value'], first['change'] - second['change']
                        if dv and dy:
                            concordant += (dv > 0) == (dy > 0); discordant += (dv > 0) != (dy > 0)
            info_pairs = [concordant, concordant + discordant]
            info.update(options_with_parent=len(picks), options_with_value=len(valued),
                        option_spearman=spearman([c['value'] for c in valued], [c['change'] for c in valued]),
                        option_sign_agreement=[agree, decided], within_parent_concordance=info_pairs,
                        mean_change_when_value_positive=(sum(c['change'] for c in valued if c['value'] > 0) / max(1, sum(c['value'] > 0 for c in valued))),
                        mean_change_when_value_negative=(sum(c['change'] for c in valued if c['value'] < 0) / max(1, sum(c['value'] < 0 for c in valued))),
                        mean_change_all=(sum(c['change'] for c in picks) / len(picks)) if picks else None)
            run['gates'][str(gate)] = info
            print('  gate %s: %4d entries, %3d pass; entry Spearman %s (HP only %s) on %d, pass AUC %s | options: %d with a parent, %d valued, '
                  'Spearman %s, mean change value>0 %+.3f, value<0 %+.3f; same-parent pairs ordered right %d / %d (%s)' % (
                      gate, info['entries'], info['passes'], fmt(info['entry_spearman']), fmt(info['hp_only_spearman']), len(known), fmt(info['entry_pass_auc']),
                      len(picks), len(valued), fmt(info['option_spearman']), info['mean_change_when_value_positive'],
                      info['mean_change_when_value_negative'], info_pairs[0], info_pairs[1],
                      '%.0f%%' % (100 * info_pairs[0] / info_pairs[1]) if info_pairs[1] else 'n/a'))
        report['runs'].append(run)
    a.out.write_text(json.dumps(report, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
