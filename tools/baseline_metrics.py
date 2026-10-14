"""Read-only accounting for completed or censored native campaigns.

Summed worker wall time is service time, not elapsed campaign wall time. A cache
hit is an evaluation but contributes no new native work. No helper infers wins.
"""
import collections
import json
from spire_exact.planning.budget_audit import clock_gate_audit


def read_ledger(path):
    if not path.exists():
        return [], [{'kind': 'missing_ledger', 'path': str(path)}]
    records, issues = [], []
    with path.open('rb') as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError('Expected an object')
            except (ValueError, UnicodeDecodeError) as error:
                # Interrupted writes are evidence gaps, not game failures. A
                # malformed interior line is also reported, never silently lost.
                issues.append({'kind': 'invalid_ledger_row', 'line': number,
                               'newline_terminated': line.endswith(b'\n'), 'error': str(error)})
                continue
            records.append(row)
    return records, issues


def ratio(numerator, denominator):
    return numerator / denominator if denominator and denominator > 0 else None


def summarize_runtime(records, resources):
    stages, actions = collections.Counter(), collections.Counter()
    wall_us = cpu_us = allocated = search_us = gc_ms = nodes = 0
    profiled = cache_hits = searches = yields = 0
    process_cycles = cycle_profiled = 0
    clock_excluded = clock_reported = clock_unproven = 0
    boundaries, failures = collections.Counter(), collections.Counter()
    by_kind = collections.defaultdict(lambda: {'evaluations': 0, 'native_seconds': 0., 'nodes': 0})
    for row in records:
        if row.get('cache_hit'):
            cache_hits += 1
            continue
        perf = row.get('performance') or {}
        metrics = row.get('advisor_metrics') or {}
        kind = by_kind[row.get('kind', 'unknown')]
        kind['evaluations'] += 1
        expanded = row.get('expanded_combat_nodes') or 0
        kind['nodes'] += expanded
        nodes += expanded
        if perf:
            profiled += 1
            if isinstance(perf.get('cycles'), int):
                process_cycles += perf['cycles']
                cycle_profiled += 1
            wall_us += perf.get('wall_us') or 0
            cpu_us += perf.get('cpu_us') or 0
            allocated += perf.get('allocated_bytes') or 0
            kind['native_seconds'] += (perf.get('wall_us') or 0) / 1e6
            actions.update(perf.get('counters') or {})
            for name, value in (perf.get('exclusive_stages') or {}).items():
                stages[name] += value.get('us') or 0
        for search in metrics.get('searches') or []:
            searches += 1
            search_us += search.get('wall_us') or 0
            gc_ms += search.get('gc_pause_ms') or 0
            yields += search.get('worker_yields') or 0
            boundaries[str(search.get('boundary'))] += 1
            clock = clock_gate_audit(search)
            clock_excluded += int(clock['clock_gates_excluded'])
            clock_reported += int(clock['reported_time_boundary'] is True)
            clock_unproven += int(not clock['clock_gates_excluded'] and clock['reported_time_boundary'] is not True)
        observation = row.get('observation') or {}
        failures[(str(row.get('classification')), observation.get('act'),
                  observation.get('floor'), observation.get('room'))] += 1
    service = wall_us / 1e6
    job_wall = resources.get('wall_seconds')
    job_cpu = resources.get('job_cpu_seconds')
    coordinator = [r['coordinator_cpu_seconds'] for r in records
                   if isinstance(r.get('coordinator_cpu_seconds'), (float, int))]
    return {
        'scope': 'absorbed non-cache evaluations; excludes unabsorbed in-flight work and independent replay',
        'evaluations': len(records), 'cache_hits': cache_hits, 'profiled_evaluations': profiled,
        'native_service_seconds_sum': service, 'native_cpu_seconds_sum': cpu_us / 1e6,
        'native_process_cycles_sum': process_cycles if cycle_profiled else None,
        'cycle_profiled_evaluations': cycle_profiled,
        'cpu_accounting_is_utilization': False,
        'native_cpu_per_service_second': ratio(cpu_us, wall_us),
        'integrated_native_service_concurrency': ratio(service, job_wall),
        'job_busy_cores': ratio(job_cpu, job_wall) if job_cpu is not None else None,
        'coordinator_cpu_seconds_last': max(coordinator) if coordinator else None,
        'exclusive_stages': [{'name': k, 'seconds_sum': v / 1e6,
                              'fraction_of_native_service': ratio(v, wall_us)}
                             for k, v in stages.most_common()],
        'allocated_bytes_sum': allocated, 'allocated_bytes_per_node': ratio(allocated, nodes),
        'combat_nodes': nodes, 'searches': searches, 'search_wall_seconds_sum': search_us / 1e6,
        'search_gc_pause_seconds_sum': gc_ms / 1000,
        'gc_pause_fraction_of_search_wall': ratio(gc_ms * 1000, search_us),
        'worker_yields': yields, 'search_boundaries': dict(boundaries),
        'clock_gate_audit': {'searches_excluded_by_sufficient_margin': clock_excluded,
                             'reported_time_boundaries': clock_reported,
                             'unproven_searches': clock_unproven,
                             'unproven_is_not_a_reported_time_hit': True},
        'action_counters': dict(actions), 'by_kind': dict(by_kind),
        'failure_locations': [{'classification': key[0], 'act': key[1], 'floor': key[2],
                               'room': key[3], 'evaluations': value}
                              for key, value in failures.most_common()],
        'retained_bytes_are_not_cumulative_writes': True,
        'job_write_transfers_include_pipes': True,
    }
