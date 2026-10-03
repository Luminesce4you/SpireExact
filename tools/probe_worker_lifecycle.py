"""Bounded native lifecycle regression; functional evidence, never speed promotion.

Uses seven production workers, retained A10 entries, an actual Windows Job cap,
and a deliberately tiny RSS threshold to exercise recycling deterministically.
No source/game/vendor files are modified. Existing output must be empty.
"""
import argparse
import hashlib
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from spire_exact.canonical import canonical
from spire_exact.mode1 import NativeCampaignBackend
from spire_exact.native import build_host
from spire_exact.planning.budget_audit import clock_gate_audit
from spire_exact.planning.io import read_json, write_json
from spire_exact.planning.pool import NativePool, WorkerError
from spire_exact.planning.resources import ResourcePlan, MIB
from tools.fight_bench import GAME_FIELDS, _affinity, _canonical, summarize, compare_exact


def run(cases_path, out, nodes, build, workers=7):
    if out.exists() and any(out.iterdir()):
        raise ValueError('Fresh output required')
    out.mkdir(parents=True, exist_ok=True)
    source = read_json(cases_path)
    cases = source['cases']
    # Fixed selection: two each Monster/Elite/Boss and one final second boss.
    selected = []
    for room in ('Monster', 'Elite', 'Boss'):
        selected.extend([c for c in cases if c['category'] == room][:2])
    selected.extend([c for c in cases if c.get('source_case_id', '').startswith('second-boss')][:1])
    if len(selected) != 7:
        raise ValueError('Need two Monster/Elite/Boss entries and a second-boss entry')
    data = ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
    plan = ResourcePlan.detect(workers, 1, 1792, 1024)
    if plan.workers != workers:
        raise ValueError(f'{workers} workers required; insufficient resources: {plan.as_dict()}')
    adapter = NativeCampaignBackend(selected[0]['context'], out, data, 'combatsolver')
    requests = []
    for case in selected:
        advisor = {**adapter.advisor, 'nodes': nodes, 'budget_ms': 600000, 'boss_budget_ms': 600000,
                   'dop': 1, 'profile': 'Low', 'reuse_continuations': True, 'quiet_diagnostics': True,
                   'fix_consumed_block_compensation': True, 'measure_search_phases': True}
        requests.append({**case['context'], 'history': case['prefix'], 'advisor': advisor,
                         'generate_candidate': True, 'policy_seed': case['policy_seed'],
                         'max_decisions': len(case['prefix'])+1500, 'stop_at_floor': case['observation']['floor'],
                         'capture_checkpoints': False, 'low_io': True, 'event_driven_settle': True})
    variants = [('control', 'server-large-gen0', 'hard', plan),
                ('recycle-stress', 'server-large-gen0', 'recycle-at-boundary', replace(plan, worker_memory_bytes=64*MIB)),
                ('bounded-gen0', 'server-bounded-large-gen0', 'hard', plan)]
    manifest = {'schema': 'spire-worker-lifecycle/v1', 'protocol': 'A10-seed-v2',
                'memory_protocol_override': '14 GiB enforced Job + 2 GiB reserve', 'build': build,
                'cases_file': str(cases_path.resolve()), 'cases_sha256': hashlib.sha256(cases_path.read_bytes()).hexdigest(),
                'cases': [c['id'] for c in selected], 'nodes': nodes, 'repeat': 2, 'resources': plan.as_dict(),
                'variants': [{'name': n, 'runtime': r, 'policy': m, 'worker_memory_bytes': p.worker_memory_bytes}
                             for n, r, m, p in variants],
                'scope': 'E-core concurrent-load functional test; artificial 64 MiB RSS stress; no performance promotion',
                'counts_as_fresh_win': False}
    write_json(out/'manifest.json', manifest)
    rows, checks, stats = [], {}, {}
    for name, runtime, policy, resources in variants:
        arm = out/name
        arm.mkdir()
        with NativePool(data, arm/'workers', resources, runtime_profile=runtime, memory_policy=policy) as pool, \
                (arm/'results.jsonl').open('w', encoding='utf-8') as sink:
            for repeat in range(2):
                pending = [(case, req, arm/f'{case["id"]}-r{repeat}',
                            pool.submit(req, arm/f'{case["id"]}-r{repeat}', 1200))
                           for case, req in zip(selected, requests)]
                for case, req, target, future in pending:
                    row = {'case': case['id'], 'repeat': repeat, 'arm': name, 'target': str(target),
                           'request_sha256': hashlib.sha256(_canonical(req)).hexdigest()}
                    try:
                        result, _ = future.result()
                        transport = read_json(target/'transport.json')
                        row.update(summarize(result, len(case['prefix']), case['observation']['floor'], transport))
                        searches = (result.get('advisor_metrics') or {}).get('searches') or []
                        evidence = result.get('decision_evidence') or []
                        n = len(case['prefix'])
                        row['checks'] = {
                            'prefix': canonical(result['trace'][:n]) == canonical(case['prefix']),
                            'entry': len(evidence)>n and canonical(evidence[n]['observation']) == canonical(case['observation']),
                            'clock_gates_excluded': bool(searches) and all(clock_gate_audit(s)['clock_gates_excluded'] for s in searches),
                            'cycles': (result.get('performance') or {}).get('cycles', 0)>0,
                            'offline': (result.get('advisor_metrics') or {}).get('counters', {}).get('offline_statistics_disabled', 0)>0,
                            'recycle': name!='recycle-stress' or transport['recycle_reason']=='rss_threshold',
                            'heap_limit': name!='bounded-gen0' or result['performance']['gc_configuration']['GCHeapHardLimit']==plan.worker_memory_bytes*3//4}
                    except Exception as error:
                        row['error'] = str(error)
                    rows.append(row)
                    sink.write(json.dumps(row)+'\n'); sink.flush()
                    print(json.dumps({'event': 'lifecycle_case', 'arm': name, 'case': case['id'], 'repeat': repeat,
                                      'error': row.get('error'), 'checks': row.get('checks')}), flush=True)
            stats[name] = dict(pool.stats)
    # The old hard policy must still reject the artificial threshold.
    with NativePool(data, out/'hard-stress-workers', replace(plan, workers=1, worker_memory_bytes=64*MIB),
                    runtime_profile='server-large-gen0') as pool:
        try:
            pool.run(requests[0], out/'hard-stress', 120)
            checks['hard_policy_stress_reproduced'] = False
        except WorkerError as error:
            checks['hard_policy_stress_reproduced'] = str(error)=='NATIVE_TASK_MEMORY_BUDGET'
    comparisons = {name: compare_exact(out/'control/results.jsonl', out/name/'results.jsonl')
                   for name in ('recycle-stress', 'bounded-gen0')}
    # Digests are only convenient report keys. Check exported game bytes directly too.
    for name in comparisons:
        for case in selected:
            for repeat in range(2):
                paths = [out/arm/f'{case["id"]}-r{repeat}'/'data/decision.json' for arm in ('control', name)]
                try:
                    results = [read_json(p) for p in paths]
                    checks[f'{name}-{case["id"]}-r{repeat}-full-game-bytes'] = \
                        canonical({k: results[0].get(k) for k in GAME_FIELDS}) == canonical({k: results[1].get(k) for k in GAME_FIELDS})
                except OSError:
                    checks[f'{name}-{case["id"]}-r{repeat}-full-game-bytes'] = False
    # Fresh processes, initial state, no advisor/checkpoints/cache for every stress trace.
    with NativePool(data, out/'verify-workers', plan, runtime_profile='server-one-heap') as pool:
        for case in selected:
            target = out/'verify'/case['id']
            try:
                result = read_json(out/'recycle-stress'/f'{case["id"]}-r0'/'data/decision.json')
                request = {**case['context'], 'history': result['trace'], 'expected_evidence': result['decision_evidence'],
                           'generate_candidate': False, 'capture_checkpoints': False, 'low_io': True}
                replay, _ = pool.run(request, target, 600, fresh=True)
                fields = [k for k in GAME_FIELDS if k not in ('status', 'phase', 'reason')]
                counters = replay['performance']['counters']
                checks['independent-'+case['id']] = (
                    canonical({k: replay.get(k) for k in fields}) == canonical({k: result.get(k) for k in fields})
                    and not replay.get('reason') and replay['performance']['pid'] != result['performance']['pid']
                    and counters['replay_prefix_solver_calls']==0 and counters['checkpoint_skipped_actions']==0)
            except Exception as error:
                checks['independent-'+case['id']] = False
                write_json(target/'verification-error.json', {'error': str(error)})
    checks['all_rows_returned'] = len(rows)==42 and all(not r.get('error') and all(r.get('checks', {}).values()) for r in rows)
    checks['all_stress_tasks_recycled'] = stats['recycle-stress']['memory_recycles']==14
    checks['both_comparisons_equivalent'] = all(r['equivalent'] for r in comparisons.values())
    report = {'passed': all(checks.values()), 'checks': checks, 'comparisons': comparisons,
              'pool_stats': stats, 'rows': rows, 'performance_promoted': False, 'counts_as_fresh_win': False}
    write_json(out/'report.json', report)
    print(json.dumps({'event': 'lifecycle_completed', 'passed': report['passed'], 'checks': checks}), flush=True)
    return report['passed']


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--cases', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--nodes', type=int, default=1000)
    p.add_argument('--workers', type=int, default=7, help='Use fewer only for functional checks, never production speed claims')
    a = p.parse_args()
    if a.nodes<1 or not 1<=a.workers<=7: p.error('positive nodes and 1..7 workers required')
    sdk = ROOT.parent/'.tools/dotnet'
    os.environ['PATH'] = str(sdk)+os.pathsep+os.environ.get('PATH', '')
    os.environ['DOTNET_ROOT'] = str(sdk)
    _affinity('e')
    _, _, build = build_host(ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64')
    os.environ['SPIRE_PROTOCOL'] = 'A10-seed-v2'
    os.environ['SPIRE_WALL_LIMIT_SECONDS'] = '1800'
    from tools import limited_cli
    sys.argv = [__file__, '--out', str(a.out)]
    def action(*args, **kwargs):
        if not run(a.cases, a.out, a.nodes, build, a.workers):
            raise SystemExit(1)
    limited_cli.runpy.run_module = action
    limited_cli.main()


if __name__ == '__main__':
    main()
