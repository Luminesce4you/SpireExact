"""Sequential P7 ABCCBA runtime benchmark; all attempts and failures are retained."""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from spire_exact.planning.io import write_json
from tools.fight_bench import compare_exact

ARMS = {'large32': ('server-large-gen0', 'hard', 32),
        'bounded32': ('server-bounded-large-gen0', 'hard', 32),
        'recycle64': ('server-large-gen0', 'recycle-at-boundary', 64)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--cases', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--solver-variants', type=Path, nargs='+', help='Compare vendor against explicit project-owned DLLs')
    a = p.parse_args()
    arms = ARMS if not a.solver_variants else {'vendor': ('server-large-gen0', 'hard', 32),
                    **{f'solver{i}': ('server-large-gen0', 'hard', 32) for i in range(len(a.solver_variants))}}
    names = list(arms)
    a.out.mkdir(parents=True, exist_ok=False)
    attempts = []
    for round_id in range(2):
        for name in (names if round_id == 0 else list(reversed(names))):
            runtime, policy, jobs = arms[name]
            target = a.out/f'r{round_id}-{name}'
            row = {'round': round_id, 'arm': name, 'out': str(target), 'state': 'running', 'started': time.time()}
            attempts.append(row)
            write_json(a.out/'status.json', {'attempts': attempts})
            command = [sys.executable, str(ROOT/'tools/run_fight_bench_job.py'), '--cases', str(a.cases.resolve()),
                       '--out', str(target.resolve()), '--workers', '7', '--cpus', 'p', '--repeat', '2',
                       '--gate-preset', 'escalate', '--normal-nodes', '10000', '--runtime-profile', runtime,
                       '--worker-memory-policy', policy, '--max-jobs', str(jobs),
                       '--advisor-json', '{"measure_search_phases":true}']
            if a.solver_variants and name != 'vendor':
                command.extend(['--solver-dll', str(a.solver_variants[int(name.removeprefix('solver'))].resolve())])
            with (a.out/f'r{round_id}-{name}.log').open('w', encoding='utf-8') as stream:
                result = subprocess.run(command, cwd=ROOT, env=dict(os.environ, PYTHONIOENCODING='utf-8'),
                                        stdout=stream, stderr=subprocess.STDOUT)
            row.update(state='finished', exit_code=result.returncode, finished=time.time())
            write_json(a.out/'status.json', {'attempts': attempts})
            print(json.dumps(row), flush=True)
            if result.returncode:
                write_json(a.out/'report.json', {'passed': False, 'attempts': attempts, 'reason': 'child failed', 'promoted': False})
                raise SystemExit(1)
    comparisons = []
    for round_id in range(2):
        for arm in names[1:]:
            first = a.out/f'r{round_id}-{names[0]}/results.jsonl'
            second = a.out/f'r{round_id}-{arm}/results.jsonl'
            comparison = compare_exact(first, second, allow_solver_change=bool(a.solver_variants))
            totals = []
            for path in (first, second):
                rows = [json.loads(s) for s in path.read_text(encoding='utf-8').splitlines()]
                totals.append({'rows': len(rows), 'errors': sum('error' in r for r in rows),
                               'search_wall': sum(r.get('search_wall', 0) for r in rows),
                               'nodes': sum(r.get('nodes', 0) for r in rows),
                               'peak_rss_mb': max((r.get('peak_rss_mb', 0) for r in rows), default=0)})
            comparisons.append({'round': round_id, 'arm': arm, 'comparison': comparison, 'totals': totals,
                                'speed_ratio': totals[0]['search_wall']/totals[1]['search_wall'] if totals[1]['search_wall'] else None})
    report = {'passed': all(c['comparison']['equivalent'] for c in comparisons),
              'attempts': attempts, 'comparisons': comparisons, 'promoted': False,
              'scope': 'P7 paired component gate; long-run stability and DEV repeats required separately'}
    write_json(a.out/'report.json', report)
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
