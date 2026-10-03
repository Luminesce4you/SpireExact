"""Paired summary of a two-arm seed panel (read-only): solved seeds, time to the first verified win.

    python tools/panel_summary.py <artifact root>/iteration-045 --arms dev-base dev-ab --cap 2700 --json summary.json

Run folders are named <arm>-<seed> (what tools/run_seed.py creates). A seed counts as solved only
with the launcher's verified_win and a certificate.json from the independent fresh replay. Unsolved
runs are censored at the cap; a pair is used once both of its runs have finished. The tests are
exact one-sided sign tests on discordant pairs (second arm better). Nothing here says a seed that
stayed unsolved cannot be won.
"""
from pathlib import Path
from math import comb
import argparse, json, sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.compare_runs import summarize


def sign_test(better: int, worse: int) -> float:
    """P(at least `better` of better + worse) under a fair coin."""
    n = better + worse
    return sum(comb(n, k) for k in range(better, n + 1)) / 2 ** n if n else 1.0


def main():
    p = argparse.ArgumentParser(); p.add_argument('folder', type=Path); p.add_argument('--arms', nargs=2, required=True, metavar=('REFERENCE', 'CANDIDATE'))
    p.add_argument('--cap', type=float, required=True, help='wall cap of every run in seconds (censoring time)'); p.add_argument('--json', type=Path)
    a = p.parse_args()
    reference, candidate = a.arms
    runs = {}
    for folder in sorted(x for x in a.folder.iterdir() if x.is_dir()):
        for arm in a.arms:
            if folder.name.startswith(arm + '-') and (folder / 'validation-manifest.json').exists():
                runs.setdefault(folder.name[len(arm) + 1:], {})[arm] = summarize(folder)
    pairs = []
    print('%-12s | %-34s | %-34s | %s' % ('seed', reference, candidate, 'first to win'))
    for seed, by in runs.items():
        cells = []
        for arm in a.arms:
            row = by.get(arm)
            if row is None: cells.append('not started'); continue
            if not row['finished']: cells.append('running: %d evals, %s s' % (row['evaluations'], row['last_absorbed_seconds'])); continue
            win = row['milestones'].get('win_candidate')
            cells.append('WIN %6.0f s, %4d evals' % (row['wall_seconds'], row['evaluations']) if row['verified_win'] else
                         'no  (%d evals, %s)' % (row['evaluations'], 'final boss' if row['milestones'].get('at_final_boss') else
                                                 'act 3' if row['milestones'].get('past_act2_boss') else 'act 2' if row['milestones'].get('past_act1_boss') else 'act 1'))
        done = all(by.get(arm) and by[arm]['finished'] for arm in a.arms)
        verdict = ''
        if done:
            r, c = by[reference], by[candidate]
            tr = r['wall_seconds'] if r['verified_win'] else a.cap
            tc = c['wall_seconds'] if c['verified_win'] else a.cap
            verdict = 'neither' if not (r['verified_win'] or c['verified_win']) else (candidate if tc < tr else reference if tr < tc else 'tie')
            pairs.append({'seed': seed, 'reference_win': r['verified_win'], 'candidate_win': c['verified_win'], 'reference_seconds': tr, 'candidate_seconds': tc,
                          'reference_evaluations': r['evaluations'], 'candidate_evaluations': c['evaluations'], 'first': verdict})
        print('%-12s | %-34s | %-34s | %s' % (seed, cells[0], cells[1], verdict))
    only_candidate = sum(x['candidate_win'] and not x['reference_win'] for x in pairs)
    only_reference = sum(x['reference_win'] and not x['candidate_win'] for x in pairs)
    faster = sum(x['first'] == candidate for x in pairs); slower = sum(x['first'] == reference for x in pairs)
    summary = {'folder': str(a.folder), 'arms': {'reference': reference, 'candidate': candidate}, 'cap_seconds': a.cap, 'complete_pairs': len(pairs),
               'solved': {reference: sum(x['reference_win'] for x in pairs), candidate: sum(x['candidate_win'] for x in pairs)},
               'solved_only_by': {reference: only_reference, candidate: only_candidate}, 'solved_by_both': sum(x['reference_win'] and x['candidate_win'] for x in pairs),
               'solved_by_neither': sum(not x['reference_win'] and not x['candidate_win'] for x in pairs),
               'sign_test_solved_one_sided_p': sign_test(only_candidate, only_reference),
               'first_to_win': {reference: slower, candidate: faster}, 'sign_test_first_to_win_one_sided_p': sign_test(faster, slower),
               'pairs': pairs, 'note': 'unsolved within the cap is censored, never evidence that a seed cannot be won'}
    print('\ncomplete pairs: %d; solved within %.0f s: %s %d, %s %d (both %d, neither %d)' % (
        len(pairs), a.cap, reference, summary['solved'][reference], candidate, summary['solved'][candidate], summary['solved_by_both'], summary['solved_by_neither']))
    print('solved by one arm only: %s %d, %s %d; exact one-sided sign test p = %.4f' % (reference, only_reference, candidate, only_candidate, summary['sign_test_solved_one_sided_p']))
    print('first to a verified win (censored at the cap): %s %d, %s %d; exact one-sided sign test p = %.4f' % (
        reference, slower, candidate, faster, summary['sign_test_first_to_win_one_sided_p']))
    if a.json:
        a.json.write_text(json.dumps(summary, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
