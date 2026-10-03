"""Where a finished run spent its boss entries, and where the winning trajectory came from (read-only).

    python tools/win_review.py <run or seed folder> [...] --out review.json

Per run: evaluations by root policy and by kind, and per boss gate the deck families (as in
tools/family_bench.py), each root policy's entries and passes, and the family the winning trajectory
entered with: how large it was, how often it passed, and how many entries the gate had absorbed
before the winning one. Descriptive only: nothing here is a bound or a claim about any state.
"""
from pathlib import Path
from collections import Counter
import argparse, ctypes, gzip, hashlib, json, sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'tools'))
from spire_exact.planning.probes import gate_entries
from family_bench import families, resolve

NATIVE = 'native'


def key_of(trace, index):
    return hashlib.sha1(json.dumps(trace[:index], sort_keys=True).encode('utf-8')).hexdigest()


def load(seed_dir):
    entries = {}; by_gate = {}; rows = []; winner = None
    for line in (seed_dir / 'evaluations.jsonl').open(encoding='utf-8'):
        try: row = json.loads(line)
        except ValueError: break
        if row.get('kind') == 'gate_probe': continue
        rows.append(row)
        if row.get('classification') not in ('NATIVE_ROUTE_DEATH', 'NATIVE_WIN_CANDIDATE') or row.get('cache_hit'): continue
        try:
            with gzip.open(seed_dir / row['label'] / 'data' / 'decision.json.gz', 'rt', encoding='utf-8') as fh: d = json.load(fh)
        except OSError: continue
        path = []
        for e in gate_entries(d):
            key = key_of(d['trace'], e['index']); path.append(key)
            if key not in entries and e['index'] >= (row.get('prefix_length') or 0):
                entries[key] = {'gate': tuple(e['gate']), 'outcome': e['outcome'], 'lost': e['lost'], 'wall': row.get('completed_wall_seconds'),
                                'policy': row.get('family') or NATIVE, 'label': row['label'], 'cards': Counter(c['id'] for c in e['entry'].get('deck') or [])}
                by_gate.setdefault(tuple(e['gate']), []).append(entries[key])
        if row['classification'] == 'NATIVE_WIN_CANDIDATE' and winner is None: winner = {'row': row, 'path': path}
    return rows, entries, by_gate, winner


def main():
    p = argparse.ArgumentParser(); p.add_argument('folders', nargs='+', type=Path); p.add_argument('--out', type=Path, required=True)
    p.add_argument('--percent', type=int, default=75); p.add_argument('--cpu-mask', type=lambda x: int(x, 0), default=0xFFFF0000)
    a = p.parse_args()
    k = ctypes.windll.kernel32; k.GetCurrentProcess.restype = ctypes.c_void_p; k.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    k.SetProcessAffinityMask(k.GetCurrentProcess(), a.cpu_mask)
    report = []
    for folder in a.folders:
        seed_dir = resolve(folder); rows, entries, by_gate, winner = load(seed_dir)
        policies = Counter(r.get('family') or NATIVE for r in rows); kinds = Counter(r.get('kind') for r in rows)
        run = {'folder': str(seed_dir), 'evaluations': len(rows), 'by_policy': dict(policies), 'by_kind': dict(kinds), 'gates': []}; report.append(run)
        print('%s: %d evaluations; by root policy %s; by kind %s' % (seed_dir, len(rows), dict(policies), dict(kinds.most_common())))
        if winner:
            w = winner['row']; run['winner'] = {'label': w['label'], 'policy': w.get('family') or NATIVE, 'wall': w.get('completed_wall_seconds'), 'kind': w.get('kind')}
            print('  first win candidate %s (%s, root policy %s) at %.0f s' % (w['label'], w.get('kind'), run['winner']['policy'], w.get('completed_wall_seconds') or 0))
        won = {entries[key]['gate']: entries[key] for key in (winner['path'] if winner else []) if key in entries}
        for gate, mine in by_gate.items():
            groups = families(mine, a.percent); passes = [e for e in mine if e['passed']]
            largest = max(groups, key=len); per_policy = {name: [sum(e['policy'] == name for e in mine), sum(e['policy'] == name for e in passes)] for name in policies}
            g = {'gate': list(gate), 'entries': len(mine), 'families': len(groups), 'passes': len(passes), 'first_entry': mine[0]['wall'],
                 'first_pass': passes[0]['wall'] if passes else None, 'by_policy': per_policy,
                 'largest': {'size': len(largest), 'passes': sum(e['passed'] for e in largest)},
                 'groups': [{'size': len(c), 'passes': sum(e['passed'] for e in c), 'first': c[0]['wall'], 'first_label': c[0]['label'], 'policies': dict(Counter(e['policy'] for e in c)),
                             'outcomes': [round(e['y'], 3) for e in c], 'walls': [round(e['wall'] or 0) for e in c]} for c in groups]}
            run['gates'].append(g)
            print('  gate %s: %d entries in %d families, %d passes; first entry %.0f s, first pass %s; entries/passes by policy %s' % (
                gate, len(mine), len(groups), len(passes), mine[0]['wall'] or 0, '%.0f s' % passes[0]['wall'] if passes else 'none', per_policy))
            print('    largest family: %d entries (%.0f%%), %d passes (%.1f%%)' % (len(largest), 100 * len(largest) / len(mine), g['largest']['passes'], 100 * g['largest']['passes'] / len(largest)))
            e = won.get(gate)
            if e is not None:
                c = groups[e['family']]; position = next(i for i, x in enumerate(mine) if x is e); inside = next(i for i, x in enumerate(c) if x is e)
                g['winner'] = {'family': e['family'], 'size': len(c), 'passes': sum(x['passed'] for x in c), 'first': c[0]['wall'], 'entry_wall': e['wall'], 'policy': e['policy'],
                               'position': position, 'position_in_family': inside, 'passes_before': sum(x['passed'] for x in mine[:position]),
                               'families_before': len({x['family'] for x in mine[:position]}), 'hp_kept': e['y'] - 1 if e['passed'] else None}
                print('    winning entry: family #%d (%d entries, %d passes, first seen %.0f s), policy %s; entered at %.0f s as entry %d of the gate (%d passes before it, %d families seen) and %d of its family; HP kept %s' % (
                    e['family'], len(c), g['winner']['passes'], c[0]['wall'] or 0, e['policy'], e['wall'] or 0, position + 1, g['winner']['passes_before'], g['winner']['families_before'],
                    inside + 1, '%.2f' % (e['y'] - 1) if e['passed'] else 'lost'))
            for c in sorted((c for c in groups if len(c) >= 5), key=lambda c: -len(c))[:6]:
                lost = [x['y'] for x in c if not x['passed']]
                print('        family of %4d: %3d passes (%5.1f%%), lost fights removed %s of the enemy life on average (best %s), first seen %5.0f s by %s, policies %s' % (
                    len(c), sum(x['passed'] for x in c), 100 * sum(x['passed'] for x in c) / len(c), '%.2f' % (sum(lost) / len(lost)) if lost else ' n/a',
                    '%.2f' % max(lost) if lost else ' n/a', c[0]['wall'] or 0, c[0]['label'], dict(Counter(x['policy'] for x in c))))
    a.out.write_text(json.dumps(report, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
