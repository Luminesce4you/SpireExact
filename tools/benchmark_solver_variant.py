"""ABBA paired native benchmark of two explicit audited solver DLLs."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.fight_bench import _affinity, compare_exact
from tools.audit_infra_bench import audit_pair
from spire_exact.planning.io import read_json, write_json


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--baseline', type=Path, required=True)
    p.add_argument('--candidate', type=Path, required=True)
    p.add_argument('--cases', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--cpus', choices=['e', 'p'], default='e')
    p.add_argument('--workers', type=int, default=2)
    a = p.parse_args()
    _affinity(a.cpus)
    a.out.mkdir(parents=True, exist_ok=False)
    attempts = []
    for name, dll in [('a0', a.baseline), ('b0', a.candidate), ('b1', a.candidate), ('a1', a.baseline)]:
        target = a.out/name
        row = {'name': name, 'solver': str(dll.resolve()), 'started': time.time(), 'state': 'running'}
        attempts.append(row)
        write_json(a.out/'status.json', {'attempts': attempts})
        cmd = [sys.executable, str(ROOT/'tools/run_fight_bench_job.py'), '--cases', str(a.cases.resolve()),
               '--out', str(target.resolve()), '--workers', str(a.workers), '--cpus', a.cpus,
               '--repeat', '2', '--gate-preset', 'escalate', '--normal-nodes', '10000',
               '--runtime-profile', 'server-large-gen0', '--solver-dll', str(dll.resolve()),
               '--advisor-json', '{"measure_search_phases":true}']
        with (a.out/(name+'.log')).open('w', encoding='utf-8') as stream:
            done = subprocess.run(cmd, cwd=ROOT, env=dict(os.environ, PYTHONIOENCODING='utf-8'), stdout=stream, stderr=subprocess.STDOUT)
        row.update(exit_code=done.returncode, finished=time.time(), state='finished')
        write_json(a.out/'status.json', {'attempts': attempts})
        print(json.dumps(row), flush=True)
        if done.returncode:
            write_json(a.out/'report.json', {'passed': False, 'attempts': attempts, 'reason': 'child failed', 'promoted': False})
            raise SystemExit(1)
        rows = [json.loads(line) for line in (target/'results.jsonl').read_text(encoding='utf-8').splitlines()]
        if any('error' in r for r in rows):
            write_json(a.out/'report.json', {'passed': False, 'attempts': attempts, 'reason': 'native case failed', 'promoted': False})
            raise SystemExit(1)
    comparisons = []
    for i in (0, 1):
        left, right = a.out/f'a{i}', a.out/f'b{i}'
        exact = compare_exact(left/'results.jsonl', right/'results.jsonl', allow_solver_change=True)
        audit = audit_pair(left, right, True)
        write_json(a.out/f'byte-audit-{i}.json', audit)
        totals = []
        for folder in (left, right):
            rows = [json.loads(line)for line in (folder/'results.jsonl').read_text(encoding='utf-8').splitlines()]
            phases = {}
            for r in rows:
                for key, value in (r.get('phase_metrics')or{}).items():
                    stat = phases.setdefault(key, {'wall_us': 0, 'allocated_bytes': 0})
                    for field in stat: stat[field] += value[field]
            resources = read_json(folder.parent/(folder.name+'-resources.json'))
            totals.append({'cases': len(rows), 'nodes': sum(r['nodes']for r in rows),
                           'search_wall': sum(r['search_wall']for r in rows),
                           'allocated_gb': sum(r['allocated_gb']for r in rows),
                           'gc_pause': sum(r['gc_pause']for r in rows), 'phase_metrics': phases,
                           'job_resources': resources})
        comparisons.append({'round': i, 'exact': exact, 'full_bytes_passed': audit['passed'], 'totals': totals})
    report = {'passed': all(c['exact']['equivalent']and c['full_bytes_passed'] for c in comparisons),
              'attempts': attempts, 'comparisons': comparisons, 'promoted': False,
              'scope': f'{a.workers} workers on {a.cpus} cores; paired component diagnostic, not production P7 validation'}
    write_json(a.out/'report.json', report)
    print(json.dumps({'passed': report['passed'], 'report': str(a.out/'report.json')}), flush=True)
    if not report['passed']: raise SystemExit(1)


if __name__ == '__main__':
    main()
