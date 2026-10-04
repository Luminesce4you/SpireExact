"""Read-only accounting for completed or censored native campaigns.

Summed worker wall time is service time, not elapsed campaign wall time. A cache
hit is an evaluation but contributes no new native work. No helper infers wins.
"""
import collections
import json
import math
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


def measured_work(row):
    """Non-cache measured subsets, with explicit completeness flags.

    New paired batches report measured work even when an arm failed. A missing
    total never erases that subset; it also never certifies zero actual work.
    Cache hits return no additional native work.
    """
    if row.get('cache_hit'):
        return {'nodes':0,'native_seconds':0.0,'search_seconds':0.0,'native_us':0,'search_us':0,
                'nodes_complete':True,'native_complete':True,'search_complete':True,
                'unknown_work_probes':0,'unknown_work_rows':0}
    def number(value):
        return type(value) in (int,float) and math.isfinite(value) and value >= 0
    measured = row.get('measured_expanded_combat_nodes')
    expanded = row.get('expanded_combat_nodes')
    count = measured if type(measured) is int and measured >= 0 else expanded
    nodes_known = type(count) is int and count >= 0
    unknown = row.get('unknown_work_probes')
    unknown = unknown if type(unknown) is int and unknown > 0 else 0
    claimed_complete = row.get('work_measurement_complete') is not False and unknown == 0
    nodes_complete = nodes_known and claimed_complete and (
        (type(expanded) is int and expanded >= 0) or row.get('work_measurement_complete') is True)
    perf = row.get('performance') or {}
    native = row.get('measured_probe_native_seconds')
    if not number(native):native = row.get('probe_native_seconds')
    if not number(native):
        wall = perf.get('wall_us')
        native = wall / 1e6 if number(wall) else None
    search = row.get('measured_probe_search_seconds')
    if not number(search):search = row.get('probe_search_seconds')
    if not number(search):
        metrics = row.get('advisor_metrics') or {}
        searches = metrics.get('searches')
        if isinstance(searches,list) and all(isinstance(s,dict) and number(s.get('wall_us')) for s in searches):
            search = sum(s['wall_us'] for s in searches) / 1e6
    native_complete = number(native) and claimed_complete
    search_complete = number(search) and claimed_complete
    return {'nodes':count if nodes_known else 0,
        'native_seconds':native if number(native) else 0.0,
        'search_seconds':search if number(search) else 0.0,
        'native_us':round(native * 1e6) if number(native) else 0,
        'search_us':round(search * 1e6) if number(search) else 0,
        'nodes_complete':nodes_complete,'native_complete':native_complete,'search_complete':search_complete,
        'unknown_work_probes':unknown,
        'unknown_work_rows':int(not(nodes_complete and native_complete and search_complete))}


def summarize_work(records):
    parts=[measured_work(row) for row in records]
    return {'combat_nodes':sum(p['nodes'] for p in parts),
        'combat_nodes_measurement_complete':all(p['nodes_complete'] for p in parts),
        'combat_nodes_unknown_rows':sum(not p['nodes_complete'] for p in parts),
        'native_service_seconds_sum':sum(p['native_us'] for p in parts) / 1e6,
        'native_service_measurement_complete':all(p['native_complete'] for p in parts),
        'search_wall_seconds_sum':sum(p['search_us'] for p in parts) / 1e6,
        'search_wall_measurement_complete':all(p['search_complete'] for p in parts),
        'unknown_work_probes':sum(p['unknown_work_probes'] for p in parts),
        'unknown_work_rows':sum(p['unknown_work_rows'] for p in parts),
        'work_sum_scope':'measured non-cache work; lower bounds when a completeness flag is false'}


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
        work = measured_work(row)
        expanded = work['nodes']
        kind['nodes'] += expanded
        nodes += expanded
        kind['native_seconds'] += work['native_seconds']
        wall_us += work['native_us']
        if perf:
            profiled += 1
            if isinstance(perf.get('cycles'), int):
                process_cycles += perf['cycles']
                cycle_profiled += 1
            cpu_us += perf.get('cpu_us') or 0
            allocated += perf.get('allocated_bytes') or 0
            actions.update(perf.get('counters') or {})
            for name, value in (perf.get('exclusive_stages') or {}).items():
                stages[name] += value.get('us') or 0
        for search in metrics.get('searches') or []:
            searches += 1
            gc_ms += search.get('gc_pause_ms') or 0
            yields += search.get('worker_yields') or 0
            boundaries[str(search.get('boundary'))] += 1
            clock = clock_gate_audit(search)
            clock_excluded += int(clock['clock_gates_excluded'])
            clock_reported += int(clock['reported_time_boundary'] is True)
            clock_unproven += int(not clock['clock_gates_excluded'] and clock['reported_time_boundary'] is not True)
        search_us += work['search_us']
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
        **summarize_work(records),
    }
