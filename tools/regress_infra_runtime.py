"""Two DEV seeds, paired count-limited fresh campaigns, repeated per arm."""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from spire_exact.canonical import canonical
from spire_exact.planning.io import read_json, write_json
from spire_exact.planning.budget_audit import clock_gate_audit
from tools.fight_bench import GAME_FIELDS


def evidence(target):
    report = read_json(target/'result.json')
    if len(report.get('evaluations', [])) != 16:
        raise ValueError('Expected all 16 count-budget evaluations')
    rows = []
    for record in report['evaluations']:
        folder = target/record['label']
        if record.get('cache_hit'):
            cached = read_json(folder/'cached.json')
            result, request = cached['result'], cached['request']
        else:
            result = read_json(folder/'data/decision.json')
            request = read_json(folder/'request.json')
        request.pop('out', None)
        if 'checkpoint' in request:
            request['checkpoint'] = Path(request['checkpoint']).name
        searches = (result.get('advisor_metrics') or {}).get('searches') or []
        if any(not clock_gate_audit(s)['clock_gates_excluded'] for s in searches):
            raise ValueError('Clock gate cannot be excluded')
        if result['status'] not in ('TERMINAL', 'BUDGET', 'DECISION'):
            raise ValueError('Unsupported/failed native evaluation')
        rows.append({'label': record['label'], 'request': request,
                     'game': {k: result.get(k) for k in GAME_FIELDS},
                     'nodes': sum(s.get('expanded_nodes', 0) for s in searches)})
    return canonical({'status': report['status'], 'rows': rows})


def run(a):
    from spire_exact.planning.__main__ import main as solve
    seeds = read_json(ROOT/'experiments/panels/A10-seed-v2/dev.json')['seeds'][:2]
    data = ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
    arms = {'control': ['--worker-memory-policy', 'hard', '--worker-max-jobs', '32', '--queue-policy', 'fifo'],
            'candidate': ['--worker-memory-policy', 'recycle-at-boundary', '--worker-max-jobs', '64', '--queue-policy', a.queue_policy]}
    checks, runs = {}, []
    for seed in seeds:
        reference = None
        for repeat in range(2):
            for arm in (list(arms) if repeat == 0 else list(reversed(arms))):
                target = a.out/f'{seed}-{arm}-r{repeat}'
                args = ['--seed', seed, '--ascension', '10', '--unlocks', 'all', '--game-dir', str(data),
                        '--out', str(target), '--workers', '7', '--dop', '1', '--worker-memory-mib', '1792',
                        '--reserve-mib', '1024', '--runtime-profile', 'server-large-gen0', '--evaluations', '16',
                        '--seconds', '1200', '--task-seconds', '600', '--nodes', '256', '--budget-ms', '600000',
                        '--boss-budget-ms', '600000', '--max-decisions', '12000', '--lookahead-actions', '12000',
                        '--lookahead-floors', '99', '--solver-seed', '271828', '--dispatch', 'ordered',
                        '--dispatch-window', '14', '--scheduler', 'focus', '--prior', '--low-io',
                        '--event-driven-settle', '--snapshot-stride', '100', *arms[arm]]
                row = {'seed': seed, 'arm': arm, 'repeat': repeat, 'target': str(target)}
                try:
                    solve(args)
                    blob = evidence(target)
                    row['sha256'] = hashlib.sha256(blob).hexdigest()
                    if reference is None: reference = blob
                    checks[target.name] = blob == reference
                    (target/'deterministic-evidence.json').write_bytes(blob)
                except Exception as error:
                    row['error'] = str(error)
                    checks[target.name] = False
                runs.append(row)
                write_json(a.out/'partial.json', {'checks': checks, 'runs': runs})
    report = {'passed': all(checks.values()) and len(checks)==8, 'checks': checks, 'runs': runs,
              'queue_policy': a.queue_policy, 'scope': 'two DEV paired repeated count regressions, not production speed or win-rate evidence'}
    write_json(a.out/'report.json', report)
    print(json.dumps({'event': 'infra_regression_complete', 'passed': report['passed']}), flush=True)
    if not report['passed']: raise SystemExit(1)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--queue-policy', choices=['fifo', 'short-prefix-first'], default='fifo')
    a = p.parse_args()
    if a.out.exists(): raise SystemExit('Fresh output required')
    sdk = ROOT.parent/'.tools/dotnet'
    os.environ['PATH'] = str(sdk)+os.pathsep+os.environ.get('PATH', '')
    os.environ['DOTNET_ROOT'] = str(sdk)
    os.environ['SPIRE_PROTOCOL'] = 'A10-seed-v2'
    os.environ['SPIRE_WALL_LIMIT_SECONDS'] = '7200'
    from tools.fight_bench import _affinity
    _affinity('p')
    from tools import limited_cli
    sys.argv = [__file__, '--out', str(a.out)]
    limited_cli.runpy.run_module = lambda *unused, **ignored: run(a)
    limited_cli.main()


if __name__ == '__main__':
    main()
