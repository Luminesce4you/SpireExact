"""Side-by-side milestones of finished or running single-seed runs (read-only).

    python tools/compare_runs.py <run folder> [<run folder> ...] [--json out.json]

A run folder is what tools/run_seed.py creates (<artifact root>/<iteration>/<name>). Only a
certificate written after the independent fresh replay counts as a win; every other number is
observed native progress, never a claim about what a seed allows.
"""
from pathlib import Path
from collections import Counter
import argparse, json, sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def summarize(folder: Path) -> dict:
    seeds = sorted(p for p in folder.glob('seed-*') if p.is_dir())
    if not seeds:
        raise SystemExit('no seed directory in ' + str(folder))
    seed = seeds[0]
    rows = []
    ledger = seed / 'evaluations.jsonl'
    if ledger.exists():
        for line in ledger.open(encoding='utf-8'):
            try: rows.append(json.loads(line))
            except ValueError: break
    report = json.loads((folder / 'baseline-report.json').read_text(encoding='utf-8')) if (folder / 'baseline-report.json').exists() else None
    manifest = json.loads((folder / 'validation-manifest.json').read_text(encoding='utf-8')) if (folder / 'validation-manifest.json').exists() else {}
    real = [r for r in rows if r.get('kind') != 'gate_probe']
    first = {}                                   # milestone -> (seconds, evaluation ordinal, label)

    def mark(name, row, ordinal):
        first.setdefault(name, (round(row.get('completed_wall_seconds') or 0, 1), ordinal, row['label']))

    floors = Counter()
    for ordinal, row in enumerate(real, 1):
        obs = row.get('observation') or {}
        act, floor = obs.get('act'), obs.get('floor')
        if act is None or floor is None:
            continue
        if row.get('classification') == 'NATIVE_ROUTE_DEATH':
            floors[(act, floor, obs.get('room'))] += 1
        if act >= 1: mark('past_act1_boss', row, ordinal)
        if act >= 2: mark('past_act2_boss', row, ordinal)
        if act >= 2 and obs.get('room') == 'Boss': mark('at_final_boss', row, ordinal)
        if row.get('classification') == 'NATIVE_WIN_CANDIDATE': mark('win_candidate', row, ordinal)
    final_boss_floors = sorted(floor for (act, floor, room) in floors if act >= 2 and room == 'Boss')
    if len(final_boss_floors) > 1:
        second = final_boss_floors[-1]
        for ordinal, row in enumerate(real, 1):
            obs = row.get('observation') or {}
            if obs.get('act') == 2 and obs.get('floor') == second:
                mark('at_second_final_boss', row, ordinal); break
    families = Counter((r.get('family') or 'native') for r in real)
    probes = [r for r in rows if r.get('kind') == 'gate_probe']
    certificate = (seed / 'certificate.json').exists()
    return {'run': folder.name, 'seed': seed.name[5:], 'settings_extra': [x for x in manifest.get('settings', []) if x.startswith('--root-pol') or x.startswith('--focus-cluster') or x.startswith('--gate-probe')],
            'version': (manifest.get('version') or '')[:12], 'finished': report is not None,
            'verified_win': bool(report and report.get('verified_win') and certificate), 'wall_seconds': round(report['wall_seconds'], 1) if report else None,
            'evaluations': len(real), 'last_absorbed_seconds': round(real[-1].get('completed_wall_seconds') or 0, 1) if real else None,
            'classifications': dict(Counter(r.get('classification') for r in real)), 'milestones': first,
            'deaths_by_act_floor_room': {'%s:%s:%s' % k: v for k, v in sorted(floors.items(), key=lambda kv: (kv[0][0], kv[0][1]))},
            'evaluations_by_root_policy': dict(families), 'probe_batches': len(probes), 'probes': sum(r.get('probes', 0) for r in probes),
            'peak_job_commit_gib': round(((report or {}).get('resources') or {}).get('peak_job_commit_bytes', 0) / 1024 ** 3, 2) if report else None}


def main():
    p = argparse.ArgumentParser(); p.add_argument('runs', nargs='+', type=Path); p.add_argument('--json', type=Path)
    a = p.parse_args()
    table = [summarize(folder) for folder in a.runs]
    names = ('past_act1_boss', 'past_act2_boss', 'at_final_boss', 'at_second_final_boss', 'win_candidate')
    print('%-24s %-11s %-5s %8s %6s  %s' % ('run', 'seed', 'win', 'wall s', 'evals', '  '.join('%-22s' % n for n in names)))
    for row in table:
        cells = []
        for name in names:
            hit = row['milestones'].get(name)
            cells.append('%-22s' % ('%.0f s / eval %d' % (hit[0], hit[1]) if hit else '-'))
        print('%-24s %-11s %-5s %8s %6d  %s' % (row['run'], row['seed'], 'YES' if row['verified_win'] else ('no' if row['finished'] else '...'),
                                               row['wall_seconds'] if row['wall_seconds'] is not None else row['last_absorbed_seconds'], row['evaluations'], '  '.join(cells)))
    if a.json:
        a.json.write_text(json.dumps(table, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
