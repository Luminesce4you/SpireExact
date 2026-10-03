"""Opt-in phase instrumentation fidelity gate on real retained combat entries."""
from pathlib import Path
import argparse
import collections
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from spire_exact.planning.io import read_json, write_json
from spire_exact.planning.pool import NativePool
from spire_exact.planning.resources import ResourcePlan
from spire_exact.canonical import canonical

GAME_KEYS = ('status', 'phase', 'reason', 'value', 'observation', 'trace',
             'decision_evidence', 'native_terminal_observed')


def select_cases(cases, second_boss):
    selected = []
    for category in ('Monster', 'Elite', 'Boss'):
        group = [c for c in cases['cases'] if c['category'] == category][:3]
        if len(group) != 3:
            raise ValueError('Three cases required for ' + category)
        selected.extend(group)
    if len(second_boss['cases']) != 3:
        raise ValueError('Three second-boss cases required')
    selected.extend(second_boss['cases'])
    return [dict(case, id=f'phase-{i:02d}', source_case_id=case['id']) for i, case in enumerate(selected)]


def totals(result):
    searches = (result.get('advisor_metrics') or {}).get('searches', [])
    return sum(s['expanded_nodes'] for s in searches)


def run(cases_path, out):
    selection = read_json(cases_path)
    cases = selection['cases']
    if out.exists() and any(out.iterdir()):
        raise ValueError('Output must be empty')
    out.mkdir(parents=True, exist_ok=True)
    data = ROOT / 'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
    rows = []; phases = collections.defaultdict(lambda: {'wall_us': 0, 'allocated_bytes': 0})
    plan = ResourcePlan.detect(7, 1, 1536, 1024)
    if plan.workers != 7:
        raise ValueError('Seven workers required')
    with NativePool(data, out / 'workers', plan, runtime_profile='server-one-heap') as pool:
        pending = []
        for i, case in enumerate(cases):
            for enabled in ((False, True) if i % 2 == 0 else (True, False)):
                target = out / (case['id'] + ('-on' if enabled else '-off'))
                advisor = {**case['advisor'], 'nodes': 60000, 'budget_ms': 600000, 'boss_budget_ms': 600000,
                           'dop': 1, 'quiet_diagnostics': True, 'fix_consumed_block_compensation': True,
                           'measure_search_phases': enabled}
                request = {**case['context'], 'history': case['prefix'], 'advisor': advisor,
                           'generate_candidate': True, 'policy_seed': case['policy_seed'],
                           'max_decisions': len(case['prefix']) + 1500, 'stop_at_floor': case['observation']['floor'],
                           'capture_checkpoints': False, 'low_io': True, 'event_driven_settle': True}
                pending.append((pool.submit(request, target, 1800), case, enabled, target))
        for future, case, enabled, target in pending:
            row = {'case': case['id'], 'enabled': enabled, 'checks': {}}
            try:
                result, identity = future.result()
                n = len(case['prefix']); evidence = result.get('decision_evidence') or []
                searches = (result.get('advisor_metrics') or {}).get('searches') or []
                phase_positive = any((p.get('wall_us') or 0) > 0 for s in searches
                                     for p in (s.get('phase_metrics') or {}).values())
                row['checks'].update(
                    entry_matches=len(evidence) > n and canonical(result['trace'][:n]) == canonical(case['prefix'])
                                  and canonical(evidence[n]['observation']) == canonical(case['observation']),
                    flag_observed=bool(searches) and all(s.get('measure_search_phases') is enabled for s in searches),
                    positive_phases=phase_positive if enabled else all(s.get('phase_metrics') is None for s in searches),
                    count_bound=all(not s.get('time_boundary') for s in searches),
                    supported=result['status'] in ('TERMINAL', 'BUDGET', 'DECISION')
                              and result.get('reason') in (None, 'candidate_horizon'))
                replay_request = {**case['context'], 'history': result['trace'], 'generate_candidate': False,
                                  'expected_evidence': evidence, 'capture_checkpoints': False,
                                  'low_io': True, 'event_driven_settle': True}
                replay, other = pool.run(replay_request, out / ('verify-' + target.name), 600, fresh=True)
                replay_keys = [k for k in GAME_KEYS if k not in ('status', 'phase', 'reason')]
                row['checks']['independent_replay'] = (
                    canonical({k: result.get(k) for k in replay_keys}) == canonical({k: replay.get(k) for k in replay_keys})
                    and canonical(identity) == canonical(other) and not replay.get('reason')
                    and result['performance']['pid'] != replay['performance']['pid']
                    and replay['performance']['counters']['replay_prefix_solver_calls'] == 0
                    and replay['performance']['counters']['checkpoint_skipped_actions'] == 0)
                row.update(nodes=totals(result), performance=result['performance'], searches=searches)
                if enabled:
                    for search in searches:
                        for name, metric in (search.get('phase_metrics') or {}).items():
                            for key in ('wall_us', 'allocated_bytes'):
                                phases[name][key] += metric[key]
            except Exception as error:
                row['error'] = str(error)
            rows.append(row)
            write_json(out / 'partial-results.json', {'rows': rows})
            print(json.dumps({'event': 'phase_case_completed', 'case': case['id'], 'enabled': enabled,
                              'passed': bool(row['checks']) and all(row['checks'].values()) and 'error' not in row}), flush=True)
    pairs = []
    for case in cases:
        try:
            a = read_json(out / (case['id'] + '-off/data/decision.json'))
            b = read_json(out / (case['id'] + '-on/data/decision.json'))
            equal = canonical({k: a.get(k) for k in GAME_KEYS}) == canonical({k: b.get(k) for k in GAME_KEYS})
            pairs.append({'case': case['id'], 'game_equal': equal, 'nodes_equal': totals(a) == totals(b)})
        except (ValueError, OSError) as error:
            pairs.append({'case': case['id'], 'error': str(error), 'game_equal': False, 'nodes_equal': False})
    passed = (len(rows) == 2 * len(cases) and all(bool(r['checks']) and all(r['checks'].values()) and 'error' not in r for r in rows)
              and all(p['game_equal'] and p['nodes_equal'] for p in pairs))
    report = {'passed': passed, 'rows': rows, 'pairs': pairs, 'phases': dict(phases),
              'metrics_accepted_for_diagnosis': passed, 'counts_as_fresh_win': False,
              'timing_includes_instrumentation_overhead': True, 'selection': str(cases_path)}
    write_json(out / 'report.json', report)
    print(json.dumps({'event': 'phase_profile_completed', 'passed': passed}), flush=True)
    return passed


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--cases', type=Path, required=True); p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    from tools import limited_cli
    sys.argv = [__file__, '--out', str(a.out)]
    def action(*args, **kwargs):
        if not run(a.cases, a.out):
            raise SystemExit(1)
    limited_cli.runpy.run_module = action
    limited_cli.main()
