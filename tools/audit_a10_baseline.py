"""Post-run evidence audit. Does not launch games or change frozen artifacts."""
from pathlib import Path
import argparse, collections, json, os, sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from spire_exact.planning.io import read_json, write_json
from spire_exact.mode1 import check_winning_replay
from spire_exact.canonical import canonical
from tools.baseline_metrics import read_ledger, summarize_runtime, ratio


def audit(run, out):
    manifest = read_json(run / 'validation-manifest.json')
    seed_dir = run / ('seed-' + str(manifest['seed']))
    result = read_json(seed_dir / 'result.json') if (seed_dir / 'result.json').exists() else {}
    resource_path = run / (seed_dir.name + '-resources.json')
    resources = read_json(resource_path) if resource_path.exists() else {}
    baseline = read_json(run / 'baseline-report.json') if (run / 'baseline-report.json').exists() else {}
    records, ledger_issues = read_ledger(seed_dir / 'evaluations.jsonl')
    classes = collections.Counter(r.get('classification', 'MISSING_CLASSIFICATION') for r in records)
    counters = collections.Counter()
    boundaries = collections.Counter()
    cumulative_nodes = 0
    curve = []
    last_floor = -1
    total_searches = 0
    search_wall_us = gc_pause_ms = 0
    errors = []
    for r in records:
        cumulative_nodes += r.get('expanded_combat_nodes', 0) if not r.get('cache_hit') else 0
        m = r.get('advisor_metrics') or {}
        counters.update(m.get('counters') or {})
        for search in m.get('searches', []):
            total_searches += 1
            boundaries[search.get('boundary')] += 1
            search_wall_us += search.get('wall_us', 0)
            gc_pause_ms += search.get('gc_pause_ms', 0)
        floor = (r.get('observation') or {}).get('floor', -1)
        if floor > last_floor or r.get('classification') == 'NATIVE_WIN_CANDIDATE':
            last_floor = max(last_floor, floor)
            curve.append({'label': r['label'], 'floor': floor,
                          'wall_seconds': r.get('completed_wall_seconds'),
                          'job_cpu_seconds': r.get('runtime', {}).get('job_cpu_seconds'),
                          'cumulative_expanded_nodes': cumulative_nodes,
                          'classification': r.get('classification')})
        if r.get('classification') not in ('NATIVE_ROUTE_DEATH', 'NATIVE_WIN_CANDIDATE'):
            errors.append({k: r.get(k) for k in ('label', 'classification', 'reason')})
    # These are telemetry records with float timings, not exact game state keys.
    checks = {'ledger_read_complete': not ledger_issues,
              'result_present': bool(result), 'resource_report_present': bool(resources),
              'ledger_matches_final_records': json.dumps(records, sort_keys=True, allow_nan=False) == json.dumps(result.get('evaluations'), sort_keys=True, allow_nan=False),
              'fresh_planner_start': manifest.get('old_prefixes_loaded') is False,
              'native_exit_zero': baseline.get('exit_code') == 0}
    witness = None
    if result.get('status') == 'VERIFIED_WIN_IN_NATIVE_HOST':
        label = result['best_label']
        candidate = read_json(seed_dir / label / 'data/decision.json')
        verification = seed_dir / ('verify-' + label)
        replay = read_json(verification / 'data/decision.json')
        request = read_json(verification / 'request.json')
        candidate_identity = {'context': result['context'], 'native': read_json(seed_dir / label / 'data/identity.json')}
        replay_identity = {'context': result['context'], 'native': read_json(verification / 'data/identity.json')}
        certificate = check_winning_replay(candidate, replay, candidate_identity, replay_identity)
        checks.update(certificate_matches=canonical(certificate) == canonical(read_json(seed_dir / 'certificate.json')),
                      replay_has_no_advisor_or_checkpoint=not request.get('advisor') and not request.get('checkpoint'),
                      replay_does_not_generate_actions=request.get('generate_candidate') is False,
                      independent_native_pid=candidate['performance']['pid'] != replay['performance']['pid'],
                      replay_zero_solver_calls=replay['performance']['counters']['replay_prefix_solver_calls'] == 0,
                      replay_no_checkpoint_skips=replay['performance']['counters']['checkpoint_skipped_actions'] == 0)
        witness = {'label': label, 'actions': len(replay['trace']), 'value': replay['value'],
                   'candidate_pid': candidate['performance']['pid'], 'replay_pid': replay['performance']['pid'],
                   'replay_wall_seconds': replay['performance']['wall_us'] / 1e6,
                   'proof_scope': certificate['proof_scope'], 'normal_godot_verified': False}
    sizes = collections.Counter()
    for base, _, files in os.walk(run):
        for name in files:
            p = Path(base) / name
            group = ('checkpoint' if p.parent.name == 'checkpoints' else name)
            sizes[group] += p.stat().st_size
    wall = resources.get('wall_seconds')
    cpu = resources.get('job_cpu_seconds')
    retained_rate = ratio(sum(sizes.values()) * 3600, wall)
    transfer = resources.get('job_write_transfer_bytes')
    transfer_rate = ratio(transfer * 3600, wall) if transfer is not None else None
    busy_cores = ratio(cpu, wall) if cpu is not None else None
    bins = []
    anchor_wall = anchor_cpu = 0
    for r in records:
        runtime = r.get('runtime') or {}
        t, c = runtime.get('job_wall_seconds'), runtime.get('job_cpu_seconds')
        if t is not None and c is not None and t - anchor_wall >= 300:
            bins.append({'start_seconds': anchor_wall, 'end_seconds': t,
                         'busy_cores': (c - anchor_cpu) / (t - anchor_wall)})
            anchor_wall, anchor_cpu = t, c
    if wall is not None and cpu is not None and wall > anchor_wall and cpu >= anchor_cpu:
        bins.append({'start_seconds': anchor_wall, 'end_seconds': wall,
                     'busy_cores': (cpu - anchor_cpu) / (wall - anchor_wall), 'final_partial_interval': True})
    transports, transport_issues = [], []
    for path in sorted(seed_dir.glob('eval-*/transport.json')):
        try:
            transports.append(read_json(path))
        except (OSError, ValueError) as error:
            transport_issues.append({'path': str(path), 'error': str(error)})
    waits = sorted(row['queue_seconds'] for row in transports if isinstance(row.get('queue_seconds'), (float, int)))
    workers = (result.get('resources') or {}).get('workers')
    service_seconds = sum(r.get('wall_seconds', 0) for r in transports)
    occupancy = ratio(service_seconds, workers * wall) if workers and wall and transports else None
    report = {'schema': 'a10-baseline-audit/v2', 'source': str(run.resolve()), 'checks': checks,
              'evidence_valid': all(checks.values()), 'witness': witness,
              'evaluations': len(records), 'classifications': dict(classes),
              'search_metrics': result.get('search_metrics'), 'progress_curve': curve,
              'ledger_issues': ledger_issues, 'final_status': result.get('status'),
              'exit_code': baseline.get('exit_code'), 'censored': witness is None,
              'performance_diagnosis': summarize_runtime(records, resources),
              'transport_summary': {
                  'scope': 'retained completed native evaluations, including any not yet absorbed; waits overlap',
                  'rows': len(transports), 'issues': transport_issues,
                  'queue_wait_seconds_sum': sum(waits),
                  'queue_wait_seconds_mean': sum(waits) / len(waits) if waits else None,
                  'queue_wait_seconds_p95': waits[min(len(waits)-1, (95*len(waits)+99)//100-1)] if waits else None,
                  'service_seconds_sum': sum(r.get('wall_seconds', 0) for r in transports)},
              'searches': total_searches, 'expanded_nodes': cumulative_nodes,
              'search_boundaries': dict(boundaries), 'advisor_counters': dict(counters),
              'search_wall_seconds_sum': search_wall_us / 1e6, 'gc_pause_seconds_sum': gc_pause_ms / 1000,
              'resources': resources, 'busy_cores': busy_cores, 'five_minute_cpu_intervals': bins,
              'cpu_accounting_is_utilization': False,
              'completed_worker_occupancy_lower_bound': occupancy,
              'retained_file_bytes': sum(sizes.values()), 'retained_bytes_per_hour': retained_rate,
              'job_transfer_bytes_per_hour': transfer_rate,
              'largest_file_categories': dict(sizes.most_common(15)),
              'm0_cpu_gate': None,  # Deprecated: Windows accounting misses yielding threads.
              'm0_worker_occupancy_gate': occupancy >= .95 if occupancy is not None else None,
              'm0_retained_write_gate': retained_rate <= 1e9 if retained_rate is not None else None,
              'm0_job_transfer_gate': transfer_rate <= 1e9 if transfer_rate is not None else None,
              'not_a_generalization_estimate': True, 'm0_complete': False}
    write_json(out / 'audit.json', report)
    write_json(out / 'non_death_outcomes.json', errors)
    print(json.dumps({k: report[k] for k in ('evidence_valid', 'witness', 'evaluations', 'classifications',
          'busy_cores', 'retained_bytes_per_hour', 'job_transfer_bytes_per_hour', 'largest_file_categories')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    audit(args.run, args.out)
