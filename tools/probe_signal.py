"""C4 of iteration-044: does a single-base probe table rank the gate's later real entries?

The table comes from probe_bench.py --mode table (one base entry, every one-card edit, HP +-10).
A real entry is predicted to first order from the base:

    base outcome + sum over cards (copies gained x add delta, copies lost x remove delta)
                 + upgrades gained x upgrade delta + HP difference x HP slope

and compared with what the native search actually got at that entry (one scale per gate: enemy HP
removed over the largest life total seen at the gate; survived fights are 1 + HP fraction kept).
The per-seed gate model's own prequential prediction is computed on the same entries for reference.
Everything here is an allocation-signal check; nothing is a bound or a claim about any state.
"""
from pathlib import Path
from collections import Counter
import argparse, ctypes, gzip, json, sys

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
from spire_exact.planning.gatemodel import GateModels, entry_features
from spire_exact.planning.probes import gate_entries, scaled


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


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--seed-dir', type=Path, required=True); p.add_argument('--table', type=Path, required=True, action='append')
    p.add_argument('--out', type=Path, required=True); p.add_argument('--cpu-mask', default='0x30000000')
    a = p.parse_args()
    k = ctypes.WinDLL('kernel32'); k.GetCurrentProcess.restype = ctypes.c_void_p; k.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    k.SetProcessAffinityMask(k.GetCurrentProcess(), int(a.cpu_mask, 16))
    seed = a.seed_dir
    rows = []
    for line in (seed / 'evaluations.jsonl').open(encoding='utf-8'):
        try: rows.append(json.loads(line))
        except ValueError: break
    result = json.loads((seed / 'result.json').read_text(encoding='utf-8')) if (seed / 'result.json').exists() else {}
    groups = result.get('lookahead_groups') or []
    macro = [r['label'] for r in rows if r['kind'].startswith('macro_')]
    parent = {label: g.get('source') for label, g in zip(macro, groups)} if len(groups) == len(macro) else {}
    for r in rows:
        if r.get('repair'): parent[r['label']] = r['repair'].get('source')
    tables = {}
    noise = {}
    for folder in a.table:
        grouped = {}
        for line in (folder / 'results.jsonl').open(encoding='utf-8'):
            probe = json.loads(line)
            # rows with a combat RNG sample are the noise check, not part of a table
            target = noise if 'sample' in probe else grouped
            target.setdefault((tuple(probe['gate']), probe['plan']), []).append(probe)
        for (gate, plan), probes in grouped.items():
            tables.setdefault(gate, []).append({'folder': folder.name, 'plan': plan, 'probes': probes, 'base': probes[0]['label']})
    wanted = set(tables)
    model = GateModels(271828)
    entries = {gate: [] for gate in wanted}            # absorption order
    seen = set()
    for row in rows:
        if row.get('classification') not in ('NATIVE_ROUTE_DEATH', 'NATIVE_WIN_CANDIDATE') or row.get('cache_hit'): continue
        try:
            with gzip.open(seed / row['label'] / 'data' / 'decision.json.gz', 'rt', encoding='utf-8') as fh: decision = json.load(fh)
        except OSError: continue
        for entry in gate_entries(decision):
            gate = tuple(entry['gate'])
            if gate not in wanted or entry['index'] < (row.get('prefix_length') or 0): continue
            key = json.dumps(decision['trace'][:entry['index']], sort_keys=True)
            if key in seen: continue
            seen.add(key)
            obs = entry['entry']
            entries[gate].append({'label': row['label'], 'outcome': entry['outcome'], 'lost': entry['lost'], 'own': model.predict(gate, obs),
                                  'deck': Counter(c['id'] for c in obs.get('deck') or []),
                                  'ups': Counter(c['id'] for c in obs.get('deck') or [] if c.get('upgrade')),
                                  'hp': float(obs.get('hp') or 0), 'max_hp': float(obs.get('max_hp') or 1), 'size': len(obs.get('deck') or []),
                                  'potions': sum(1 for x in obs.get('potions') or [] if x), 'relics': len(obs.get('relics') or [])})
        model.add(decision, row['label'], parent.get(row['label']))
    model.refit()
    report = {'seed_dir': str(seed), 'gates': {}}
    for gate, runs in sorted(tables.items()):
        mine = entries[gate]
        total = max([e['lost'][1] for e in mine if e['lost']] + [p['result']['lost'][1] for t in runs for p in t['probes'] if p.get('result') and p['result']['lost']] + [0.0])
        for e in mine:
            e['y'] = e['outcome'] if e['lost'] is None else (min(1.0, e['lost'][0] / total) if total else 0.0)
        final = model.gates.get(gate)
        for table in runs:
            probes = table['probes']
            base_row = next(p for p in probes if p['edit'] == 'base')
            base_index = next(i for i, e in enumerate(mine) if e['label'] == table['base'])
            base = mine[base_index]
            usable = [p for p in probes if p.get('result')]
            b = scaled(base_row['result'], total)
            delta = {p['edit']: scaled(p['result'], total) - b for p in usable if p['edit'] != 'base'}
            add = {k[5:]: v for k, v in delta.items() if k.startswith('card:')}
            remove = {k[7:]: v for k, v in delta.items() if k.startswith('remove:')}
            upgrade = {k[8:]: v for k, v in delta.items() if k.startswith('upgrade:')}
            hp_rows = {k: v for k, v in delta.items() if k.startswith('#hp')}
            slope = (hp_rows.get('#hp+10', 0.0) - hp_rows.get('#hp-10', 0.0)) / (10.0 * len(hp_rows)) if hp_rows else 0.0

            def predict(e, hp_term=True):
                value = b
                for card in set(e['deck']) | set(base['deck']):
                    change = e['deck'][card] - base['deck'][card]
                    if change > 0: value += change * add.get(card, 0.0)
                    elif change < 0: value += -change * remove.get(card, 0.0)
                for card in set(e['ups']) | set(base['ups']):
                    value += max(0, e['ups'][card] - base['ups'][card]) * upgrade.get(card, 0.0)
                return value + (slope * (e['hp'] - base['hp']) if hp_term else 0.0)

            later = mine[base_index + 1:]
            def rho(values, subset=later): return spearman(values, [e['y'] for e in subset])
            covered = []
            for e in later:
                changed = [(card, e['deck'][card] - base['deck'][card]) for card in set(e['deck']) | set(base['deck']) if e['deck'][card] != base['deck'][card]]
                known = sum(abs(c) for card, c in changed if (card in add if c > 0 else card in remove))
                covered.append(known / max(1, sum(abs(c) for _, c in changed)))
            with_model = [e for e in later if e['own'] is not None]
            item = {}
            if final is not None and final.fitted:
                item = {k[5:]: GateModels._item(final, k) for k in final.z if k.startswith('card:')}
            shared = sorted(set(add) & set(item))
            info = {'encounter': base_row['encounter'], 'plan': table['plan'], 'base': table['base'], 'base_position': base_index,
                    'base_outcome_probe': b, 'base_outcome_real': base['y'], 'entries_after_base': len(later),
                    'passes_after_base': sum(e['y'] >= 1 for e in later), 'life_total': total,
                    'probes': len(probes), 'usable_probes': len(usable), 'failed': [p['edit'] for p in probes if not p.get('result')],
                    'probe_wall_seconds': round(sum(p.get('wall') or 0 for p in probes), 1),
                    'probe_search_seconds': round(sum((p.get('result') or {}).get('search_seconds') or 0 for p in probes), 1),
                    'hp_slope_per_point': slope, 'delta_spread': {'min': min(delta.values()), 'max': max(delta.values()),
                                                                  'nonzero': sum(v != 0 for v in delta.values()), 'count': len(delta)},
                    'mean_share_of_deck_difference_covered': sum(covered) / max(1, len(covered)),
                    'spearman': {'table': rho([predict(e) for e in later]), 'table_without_hp': rho([predict(e, False) for e in later]),
                                 'hp_fraction_only': rho([e['hp'] / e['max_hp'] for e in later]), 'deck_size_only': rho([e['size'] for e in later]),
                                 'gate_model_prequential': spearman([e['own'] for e in with_model], [e['y'] for e in with_model]),
                                 'table_on_entries_with_model': spearman([predict(e) for e in with_model], [e['y'] for e in with_model]),
                                 'n_with_model': len(with_model)},
                    'add_delta_vs_final_model_item': {'cards': len(shared), 'spearman': spearman([add[c] for c in shared], [item[c] for c in shared])},
                    'top_add': sorted(add.items(), key=lambda kv: -kv[1])[:8], 'bottom_add': sorted(add.items(), key=lambda kv: kv[1])[:5],
                    'remove': sorted(remove.items(), key=lambda kv: -kv[1]), 'upgrade': sorted(upgrade.items(), key=lambda kv: -kv[1])}
            for window in (24, 48, 96):
                head = later[:window]
                if len(head) >= 8:
                    info['spearman']['table_first_%d' % window] = spearman([predict(e) for e in head], [e['y'] for e in head])
            report['gates'].setdefault(str(gate), []).append(info)
            s = info['spearman']
            fmt = lambda v: 'n/a' if v is None else format(v, '+.3f')
            print('gate %s %s plan %s: base %s at position %d, probe %.3f / real %.3f; %d later entries (%d pass); probes %d (%d usable), %.0f s wall' % (
                gate, info['encounter'], table['plan'], table['base'], base_index, b, base['y'], len(later), info['passes_after_base'],
                len(probes), len(usable), info['probe_wall_seconds']))
            print('   Spearman vs real: table %s  (no HP term %s; first 24 / 48 / 96: %s / %s / %s)  HP only %s  deck size only %s' % (
                fmt(s['table']), fmt(s['table_without_hp']), fmt(s.get('table_first_24')), fmt(s.get('table_first_48')), fmt(s.get('table_first_96')),
                fmt(s['hp_fraction_only']), fmt(s['deck_size_only'])))
            print('   on the %d entries that had a model prediction: gate model (prequential) %s, table %s; add deltas vs final model item values %s on %d cards' % (
                s['n_with_model'], fmt(s['gate_model_prequential']), fmt(s['table_on_entries_with_model']),
                fmt(info['add_delta_vs_final_model_item']['spearman']), len(shared)))
            print('   deltas: %d of %d nonzero, range %+.3f .. %+.3f; HP slope %+.4f per point; deck difference covered %.0f%%' % (
                info['delta_spread']['nonzero'], len(delta), info['delta_spread']['min'], info['delta_spread']['max'], slope,
                100 * info['mean_share_of_deck_difference_covered']))
            print('   best additions: ' + ', '.join('%s %+.3f' % kv for kv in info['top_add']))
            print('   removals: ' + ', '.join('%s %+.3f' % kv for kv in info['remove'][:6]) + ' ... ' + ', '.join('%s %+.3f' % kv for kv in info['remove'][-3:]))
            samples = noise.get((gate, table['plan']))
            if samples:
                # Same deck, other combat RNG samples: how much of a delta is the draw order?
                import statistics
                by_edit = {}
                for probe in samples:
                    if probe.get('result'): by_edit.setdefault(probe['edit'], {})[probe['sample']] = scaled(probe['result'], total)
                base_samples = by_edit.get('base', {})
                values = list(base_samples.values())
                info['noise'] = {'base_samples': values, 'base_sd': statistics.pstdev(values) if len(values) > 1 else None,
                                 'base_passes': sum(v >= 1 for v in values), 'edits': {}}
                print('   noise: unedited base under %d RNG samples: mean %.3f sd %.3f, range %.3f .. %.3f, passes %d (real fight %.3f)' % (
                    len(values), statistics.mean(values), info['noise']['base_sd'] or 0, min(values), max(values), info['noise']['base_passes'], b))
                for edit, by_sample in sorted(by_edit.items()):
                    if edit == 'base': continue
                    paired = [by_sample[s] - base_samples[s] for s in sorted(by_sample) if s in base_samples]
                    info['noise']['edits'][edit] = {'paired_deltas': paired, 'table_delta': delta.get(edit)}
                    print('      %-28s table delta %+.3f; same-sample deltas %s' % (edit, delta.get(edit, float('nan')), ' '.join('%+.3f' % d for d in paired)))
    a.out.write_text(json.dumps(report, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
