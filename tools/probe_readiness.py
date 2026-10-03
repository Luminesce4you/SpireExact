"""C5 of iteration-044: does the mean of a few RNG-sample probes say how a deck family really fares?

Input: probe_bench.py --mode readiness (families.json + results.jsonl). For every probed deck family
of a gate the first entry was re-fought under several combat RNG samples. The other entries of the
family are real fights of nearly the same deck (small deck / HP / draw-order differences), so their
mean outcome and pass rate are what the search really got from "re-rolling" that deck.

Compared across the families of one gate:
  probe mean          mean probe outcome of the first entry over the RNG samples
  single real fight   the first entry's own real outcome (what the search sees without probes)
against the mean real outcome (and pass rate) of the family's other entries.
Allocation-signal check only; no bound, no claim about any state.
"""
from pathlib import Path
import argparse, json, statistics, sys

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
from spire_exact.planning.probes import scaled


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
    if n < 4: return None
    rx, ry = rank(x), rank(y); mx, my = sum(rx) / n, sum(ry) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry)); sxx = sum((a - mx) ** 2 for a in rx); syy = sum((b - my) ** 2 for b in ry)
    return sxy / (sxx * syy) ** .5 if sxx and syy else None


def main():
    p = argparse.ArgumentParser(); p.add_argument('folder', type=Path, nargs='+'); p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    report = {'runs': []}
    pooled = {'probe': [], 'single': [], 'truth': []}
    fmt = lambda v: ' n/a ' if v is None else format(v, '+.2f')
    for folder in a.folder:
        families = json.loads((folder / 'families.json').read_text(encoding='utf-8'))
        probes = [json.loads(line) for line in (folder / 'results.jsonl').open(encoding='utf-8')]
        run = {'folder': str(folder), 'gates': {}}
        for gate, rows in sorted(families.items()):
            mine = [pr for pr in probes if '%d,%d' % tuple(pr['gate']) == gate and pr.get('result')]
            total = max([m['lost'][1] for f in rows for m in f['members'] if m['lost']] + [pr['result']['lost'][1] for pr in mine if pr['result']['lost']] + [0.0])
            def real(m): return m['outcome'] if m['lost'] is None else (min(1.0, m['lost'][0] / total) if total else 0.0)
            table = []
            for family in rows:
                samples = [scaled(pr['result'], total) for pr in mine if pr['family'] == family['family']]
                others = [real(m) for m in family['members'][1:]]
                if len(samples) < 3 or len(others) < 3: continue
                table.append({'family': family['family'], 'size': len(family['members']), 'single': real(family['members'][0]),
                              'probe_mean': statistics.mean(samples), 'probe_sd': statistics.pstdev(samples), 'probe_max': max(samples),
                              'probe_passes': sum(s >= 1 for s in samples), 'samples': len(samples),
                              'others_mean': statistics.mean(others), 'others_pass_rate': sum(o >= 1 for o in others) / len(others),
                              'others_sd': statistics.pstdev(others), 'encounter': mine[0]['encounter'] if mine else None})
            if not table: continue
            truth = [r['others_mean'] for r in table]
            info = {'families': table, 'life_total': total,
                    'spearman_probe_mean_vs_family': spearman([r['probe_mean'] for r in table], truth),
                    'spearman_single_fight_vs_family': spearman([r['single'] for r in table], truth),
                    'spearman_probe_mean_vs_pass_rate': spearman([r['probe_mean'] for r in table], [r['others_pass_rate'] for r in table]),
                    'spearman_single_fight_vs_pass_rate': spearman([r['single'] for r in table], [r['others_pass_rate'] for r in table])}
            run['gates'][gate] = info
            print('%s gate %s %s: %d families' % (folder.name, gate, table[0]['encounter'], len(table)))
            print('   family size   first entry (real)   probe mean (sd, passes)    other entries: mean real, pass rate')
            for r in sorted(table, key=lambda r: -r['others_mean']):
                print('   %5d %5d   %8.3f             %6.3f (%.3f, %d / %d)        %6.3f   %4.0f%%' % (
                    r['family'], r['size'], r['single'], r['probe_mean'], r['probe_sd'], r['probe_passes'], r['samples'], r['others_mean'], 100 * r['others_pass_rate']))
            print('   Spearman with the family mean: probe mean %s, single real fight %s;  with the family pass rate: probe mean %s, single real fight %s' % (
                fmt(info['spearman_probe_mean_vs_family']), fmt(info['spearman_single_fight_vs_family']),
                fmt(info['spearman_probe_mean_vs_pass_rate']), fmt(info['spearman_single_fight_vs_pass_rate'])))
            if len(table) >= 3:
                # within-gate standardised values, pooled over gates
                for key, values in (('probe', [r['probe_mean'] for r in table]), ('single', [r['single'] for r in table]), ('truth', truth)):
                    mean, sd = statistics.mean(values), statistics.pstdev(values) or 1.0
                    pooled[key] += [(v - mean) / sd for v in values]
        report['runs'].append(run)
    report['pooled'] = {'families': len(pooled['truth']), 'spearman_probe_mean': spearman(pooled['probe'], pooled['truth']),
                        'spearman_single_fight': spearman(pooled['single'], pooled['truth'])}
    print('pooled over gates (%d families, values standardised within each gate): Spearman with the family mean: probe mean %s, single real fight %s' % (
        len(pooled['truth']), fmt(report['pooled']['spearman_probe_mean']), fmt(report['pooled']['spearman_single_fight'])))
    a.out.write_text(json.dumps(report, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
