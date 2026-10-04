"""Summarize completed B1 P7 pairs without launching native work or promoting a DLL.

The controller's status.json and retained byte-audit reports are authoritative.
This script additionally checks warm-process continuity and admitted resources.
Ratios use the sum of per-request search wall seconds, not batch elapsed time.
The retained entry set is component evidence; its entries/repeats are correlated.
"""
import argparse
from collections import defaultdict
import csv
import ctypes
import json
import math
from pathlib import Path
import statistics
import os

MIB = 1 << 20
METRICS = ('nodes', 'search_wall', 'allocated_gb', 'gc_pause')
# Registered before the candidate timing arms; retain their observed load.
BACKGROUND_ALLOWLIST = {'AppHelperCap', 'msedgewebview2', 'OmenCommandCenterBackground', 'svchost'}
COMPUTE_PROCESS_NAMES = {'python', 'pythonw', 'dotnet', 'blender', 'FreeCAD', 'MATLAB',
                         'java', 'javaw', 'Rscript', 'ffmpeg', '7z', 'godot', 'sts2'}


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def result_rows(folder):
    result = {}
    for number, line in enumerate((folder / 'results.jsonl').read_text(encoding='utf-8').splitlines()):
        row = json.loads(line)
        key = (row['case'], row['repeat'])
        if key in result:
            raise ValueError(f'{folder}: duplicate case/repeat {key}')
        if row.get('error') or row.get('status') not in ('BUDGET', 'DECISION', 'TERMINAL'):
            raise ValueError(f'{folder}: failed native row {number}')
        for metric in METRICS:
            value = row.get(metric)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError(f'{folder}: invalid {metric} at row {number}')
        result[key] = row
    if not result:
        raise ValueError(f'{folder}: empty result set')
    return result


def totals(records):
    return {metric: sum(row[metric] for row in records) for metric in METRICS}


def ratio(candidate, baseline):
    return candidate / baseline if baseline > 0 else None


def paired_summary(records):
    arms = {arm: totals([r[arm] for r in records]) for arm in ('baseline', 'candidate')}
    value = ratio(arms['candidate']['search_wall'], arms['baseline']['search_wall'])
    return {'paired_entries': len(records), **arms, 'candidate_over_baseline': value,
            'search_wall_decrease_percent': 100 * (1 - value) if value is not None else None,
            'throughput_increase_percent': 100 * (1 / value - 1) if value else None,
            'nodes_equal': arms['baseline']['nodes'] == arms['candidate']['nodes'],
            'allocated_ratio': ratio(arms['candidate']['allocated_gb'], arms['baseline']['allocated_gb']),
            'gc_pause_ratio': ratio(arms['candidate']['gc_pause'], arms['baseline']['gc_pause'])}


def background_summary(samples):
    """Observed deltas only; samples cover the whole child arm, including warmup."""
    deltas, sample_totals, by_process = [], [], defaultdict(list)
    malformed = 0
    for sample in samples:
        sample_sum = 0
        for process in sample.get('processes', []):
            delta = process.get('cpu_delta')
            if type(delta) not in (int, float) or not math.isfinite(delta) or delta < 0:
                malformed += 1
                continue
            deltas.append(delta)
            sample_sum += delta
            by_process[process.get('name'), process.get('pid')].append(delta)
        sample_totals.append(sample_sum)
    limits = lambda values: [min(values), max(values)] if values else None
    return {'samples': len(samples), 'sampled_cpu_seconds': sum(deltas),
            'process_cpu_delta_range_seconds': limits(deltas),
            'total_cpu_delta_per_sample_range_seconds': limits(sample_totals),
            'malformed_deltas': malformed,
            'processes': [{'name': name, 'pid': pid, 'observations': len(values),
                           'sampled_cpu_seconds': sum(values), 'cpu_delta_range_seconds': limits(values)}
                          for (name, pid), values in sorted(by_process.items(), key=lambda row: str(row[0]))],
            'scope': '10-second process sampling across the entire child arm, including build/warmup/prefix replay; cannot prove zero noise or attribute load only to measured search'}


def analyze(root, *, worker_mib=1024, reserve_mib=512, rounds=4, seed_count=3, cases_per_seed=21):
    status = read(root / 'status.json')
    issues, case_pairs, arm_checks = [], [], []
    seen_pairs = set()

    def require(condition, message):
        if not condition:
            issues.append(message)

    attempts = {row['name']: row for row in status.get('attempts', [])}
    reference_gc = None
    reference_cases = {}
    reference_host = None
    reference_efficiency = None
    background_by_round_arm = defaultdict(list)
    for pair in status.get('pairs', []):
        round_id, seed = pair['round'], str(pair['seed'])
        key = (round_id, seed)
        require(key not in seen_pairs, f'duplicate completed pair {key}')
        seen_pairs.add(key)
        require(pair.get('order') == (['baseline', 'candidate'] if round_id % 2 == 0 else ['candidate', 'baseline']),
                f'{key}: unexpected alternating order')
        exact = pair.get('exact', {})
        require(exact.get('equivalent') is True and not exact.get('issues'), f'{key}: strict row comparison failed')
        for gate in ('full_bytes_passed', 'semantic_passed', 'clock_gates_excluded'):
            require(pair.get(gate) is True, f'{key}: controller {gate} did not pass')
        audit = read(root / f'byte-audit-r{round_id}-{seed}.json')
        require(audit.get('passed') is True, f'{key}: full byte audit did not pass')
        checks = audit.get('rows', [])
        require(len(checks) == cases_per_seed, f'{key}: byte audit case count differs')
        require(len({r['case'] for r in checks}) == len(checks), f'{key}: duplicated byte audit cases')
        require(all(all(row.get(field) is True for field in (
            'request_bytes', 'game_bytes', 'nodes', 'supported', 'clock_gates_excluded', 'offline')) for row in checks),
            f'{key}: a required byte/clock/offline check failed or is missing')
        records = {}
        for arm in ('baseline', 'candidate'):
            name = f'r{round_id}-{seed}-{arm}'
            attempt = attempts.get(name, {})
            folder = Path(attempt['source']) if attempt.get('source') else root / name
            require(Path(audit['left' if arm == 'baseline' else 'right']).resolve() == folder.resolve(),
                    f'{key}: byte audit refers to another {arm} directory')
            records[arm] = result_rows(folder)
            require(len(records[arm]) == cases_per_seed, f'{folder.name}: expected {cases_per_seed} cases')
            require(all(repeat == 0 for _, repeat in records[arm]), f'{folder.name}: unexpected repeat IDs')
            manifest = read(folder / 'manifest.json')
            resources = manifest.get('resources', {})
            cpus = manifest.get('affinity', {}).get('cpus', [])
            expected_resource = {'workers': 7, 'dop': 1, 'worker_memory_bytes': worker_mib * MIB,
                                 'reserve_bytes': reserve_mib * MIB}
            resources_ok = all(resources.get(k) == v for k, v in expected_resource.items())
            require(resources_ok, f'{folder.name}: admitted worker/RSS/reserve configuration differs')
            require(cpus == list(range(0, 16, 2)) and sum(1 << cpu for cpu in cpus) == 0x5555,
                    f'{folder.name}: affinity is not the eight physical P-core threads (mask 0x5555)')
            require(resources.get('effective_cpus') == 8, f'{folder.name}: effective CPU count differs')
            require(manifest.get('runtime_profile') == 'server-bounded-large-gen0', f'{folder.name}: runtime profile differs')
            require(manifest.get('worker_memory_policy') == 'hard', f'{folder.name}: hard RSS policy is required')
            require(manifest.get('repeat') == 1 and manifest.get('queue_policy') == 'fifo', f'{folder.name}: repeat/queue policy differs')
            require(manifest.get('counts_as_planner_result') is False, f'{folder.name}: unexpected campaign evidence flag')
            host = manifest.get('host_sha256')
            if reference_host is None:
                reference_host = host
            require(host is not None and host == reference_host, f'{folder.name}: host binary changed between arms/rounds')
            efficiency = manifest.get('affinity', {}).get('efficiency_class')
            if reference_efficiency is None:
                reference_efficiency = efficiency
            require(efficiency is not None and efficiency == reference_efficiency, f'{folder.name}: core class changed')
            cases_sha = manifest.get('cases_sha256')
            reference_cases.setdefault(seed, cases_sha)
            require(cases_sha is not None and cases_sha == reference_cases[seed], f'{folder.name}: retained case set changed')
            require(attempt.get('state') in ('finished', 'reused') and attempt.get('exit_code') == 0,
                    f'{folder.name}: authoritative attempt did not finish successfully')
            warm = [read(p) for p in sorted(folder.glob('warm-*/transport.json'))]
            measured = [read(p) for p in sorted(folder.glob('case-*/transport.json'))]
            warm_pids = {r['pid'] for r in warm}
            measured_pids = {r['pid'] for r in measured}
            owned = warm_pids | measured_pids | set(attempt.get('owned_worker_pids', []))
            if attempt.get('pid') is not None:
                owned.add(attempt['pid'])
            original_interference = attempt.get('interference', [])
            ignored_owned = [r for r in original_interference if r.get('pid') in owned]
            ignored_system = [r for r in original_interference
                              if r.get('pid') not in owned and r.get('name') not in COMPUTE_PROCESS_NAMES]
            unexplained = [r for r in original_interference
                           if r.get('pid') not in owned and r.get('name') in COMPUTE_PROCESS_NAMES]
            require(not unexplained, f'{folder.name}: unexplained external compute interference was recorded')
            original_background = attempt.get('background_load_samples', [])
            background = [{**sample, 'processes': [r for r in sample.get('processes', []) if r.get('pid') not in owned]}
                          for sample in original_background]
            background_by_round_arm[round_id, arm].extend(background)
            background_report = background_summary(background)
            require(background_report['malformed_deltas'] == 0, f'{folder.name}: invalid recorded background CPU delta')
            starts = sorted({r.get('worker_starts') for r in warm + measured}, key=lambda value: str(value))
            continuity = (len(warm) == cases_per_seed and len(measured) == cases_per_seed
                          and len(warm_pids) == 7 and len(measured_pids) == 7 and measured_pids <= warm_pids
                          and starts == [1] and all(r.get('worker_job', 0) > 1 for r in measured)
                          and not any(r.get('recycle_reason') for r in warm + measured))
            require(continuity, f'{folder.name}: measured worker was cold, restarted, recycled, or lacks warmup evidence')
            registered_warm = attempt.get('warm_worker_pids', attempt.get('owned_worker_pids', []))
            require(set(registered_warm) == warm_pids, f'{folder.name}: controller warm PID inventory differs')
            require(all(type(r.get('peak_sampled_rss')) is int and 0 <= r['peak_sampled_rss'] <= worker_mib * MIB
                        for r in warm + measured), f'{folder.name}: sampled RSS exceeded configured worker limit')
            for row in records[arm].values():
                gc = row.get('gc_configuration', {})
                require(gc.get('GCHeapHardLimit') == worker_mib * MIB * 3 // 4
                        and gc.get('HeapCount') == 1 and gc.get('ServerGC') is True
                        and gc.get('ConcurrentGC') is True and gc.get('NoAffinitize') is True,
                        f'{folder.name}: observed CLR GC hard limit/heap configuration differs')
                require(all(not gc.get(field) for field in ('GCHeapHardLimitPercent', 'GCHeapHardLimitSOH',
                    'GCHeapHardLimitLOH', 'GCHeapHardLimitPOH', 'GCHeapHardLimitSOHPercent',
                    'GCHeapHardLimitLOHPercent', 'GCHeapHardLimitPOHPercent')), f'{folder.name}: observed per-object GC limit overrides total cap')
                if reference_gc is None:
                    reference_gc = gc
                require(gc == reference_gc, f'{folder.name}: observed GC settings changed between native requests')
            arm_checks.append({'round': round_id, 'seed': seed, 'arm': arm, 'source': str(folder.resolve()),
                               'attempt_state': attempt.get('state'), 'warmup_passed': continuity,
                               'warm_pids': sorted(warm_pids), 'measured_pids': sorted(measured_pids),
                               'worker_starts': starts, 'resource_configuration_passed': resources_ok,
                               'peak_sampled_rss_mib': max((r['peak_sampled_rss'] / MIB for r in warm + measured), default=None),
                               'background_load': background_report,
                               'excluded_owned_background_records': sum(r.get('pid') in owned for sample in original_background
                                                                         for r in sample.get('processes', [])),
                               'original_interference': original_interference,
                               'ignored_owned_interference': ignored_owned,
                               'registered_system_interference': ignored_system,
                               'unexplained_interference': unexplained,
                               'legacy_system_interference_scope': 'Preserved separately; legacy records lack sample times, so no fabricated background sampling interval.'})
        require(records['baseline'].keys() == records['candidate'].keys(), f'{key}: per-entry pair sets differ')
        require(exact.get('paired_repeats') == cases_per_seed and exact.get('identical_repeats') == cases_per_seed,
                f'{key}: strict comparison did not cover every retained entry')
        for case_key in sorted(records['baseline'].keys() & records['candidate'].keys()):
            baseline, candidate = (records[arm][case_key] for arm in ('baseline', 'candidate'))
            require(all(baseline.get(field) is not None and baseline.get(field) == candidate.get(field)
                        for field in ('algorithm_request_sha256', 'trace_sha256', 'game_sha256', 'nodes')),
                    f'{key}/{case_key}: request/actions/game/nodes index evidence differs')
            case_pairs.append({'round': round_id, 'seed': seed, 'case': case_key[0], 'repeat': case_key[1],
                               'room': baseline.get('room'), 'floor': baseline.get('floor'),
                               'baseline': {k: baseline[k] for k in METRICS},
                               'candidate': {k: candidate[k] for k in METRICS},
                               'candidate_over_baseline': ratio(candidate['search_wall'], baseline['search_wall'])})

    round_sets = {index: {seed for index2, seed in seen_pairs if index2 == index} for index in range(rounds)}
    require(status.get('mode') == 'performance', 'authoritative run is not a performance run')
    require(status.get('state') == 'complete', 'authoritative run has not completed')
    for gate in ('passed', 'semantic_passed', 'clock_gates_excluded'):
        require(status.get(gate) is True, f'authoritative overall {gate} did not pass')
    require(len(seen_pairs) == rounds * seed_count and all(len(seeds) == seed_count for seeds in round_sets.values())
            and all(seeds == round_sets[0] for seeds in round_sets.values()), 'not all four rounds and three seeds are complete')
    require(len(case_pairs) == rounds * seed_count * cases_per_seed, 'completed paired-entry count differs')
    combined = paired_summary(case_pairs)
    if status.get('state') == 'complete' and combined['candidate_over_baseline'] is not None:
        recorded_ratio = status.get('combined_ratio')
        require(type(recorded_ratio) in (int, float) and math.isclose(recorded_ratio,
                combined['candidate_over_baseline'], rel_tol=1e-12, abs_tol=1e-12),
                'authoritative combined ratio differs from retained native rows')
    round_summaries = [{'round': index, **paired_summary([r for r in case_pairs if r['round'] == index])}
                       for index in range(rounds)]
    seed_summaries = [{'seed': seed, **paired_summary([r for r in case_pairs if r['seed'] == seed])}
                      for seed in sorted({r['seed'] for r in case_pairs})]
    entries = defaultdict(list)
    for row in case_pairs:
        entries[row['seed'], row['case']].append(row)
    entry_summaries = []
    for (seed, case), records in sorted(entries.items()):
        values = [r['candidate_over_baseline'] for r in records if r['candidate_over_baseline'] is not None]
        entry_summaries.append({'seed': seed, 'case': case, **paired_summary(records),
                                'round_ratios': [r['candidate_over_baseline'] for r in sorted(records, key=lambda r: r['round'])],
                                'median_round_ratio': statistics.median(values) if values else None,
                                'faster_rounds': sum(value < 1 for value in values)})
    valid = not issues
    value = combined['candidate_over_baseline']
    all_same_direction = all(r['candidate_over_baseline'] is not None and r['candidate_over_baseline'] < 1
                             for r in round_summaries)
    return {'schema': 'spire-b1-analysis/v1', 'root': str(root.resolve()),
            'authoritative_state': status.get('state'), 'controller_sha256': status.get('controller_sha256'),
            'timing_measurement_valid': valid, 'issues': sorted(set(issues)), 'combined': combined,
            'rounds': round_summaries, 'seeds': seed_summaries, 'entries': entry_summaries,
            'case_pairs': case_pairs, 'arm_checks': arm_checks,
            'registered_background_allowlist': sorted(BACKGROUND_ALLOWLIST),
            'background_round_arms': [{'round': index, 'arm': arm, **background_summary(samples)}
                                      for (index, arm), samples in sorted(background_by_round_arm.items())],
            'all_rounds_faster': all_same_direction,
            'step_timing_passed': valid and value is not None and value <= .97 and all_same_direction,
            'whole_b1_timing_passed': valid and value is not None and value <= .90 and all_same_direction,
            'campaign_regressions_still_required_if_timing_passes': 2, 'promoted': False,
            'scope': 'fixed retained component entries, no campaign or general scaling claim; known-process sampling cannot prove absence of short interference',
            'precision': 'input search_wall/gc_pause rounded to 0.001 s; allocated_gb rounded to 0.001 GiB by fight_bench'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--worker-memory-mib', type=int, default=1024)
    parser.add_argument('--reserve-mib', type=int, default=512)
    parser.add_argument('--rounds', type=int, default=4)
    parser.add_argument('--seed-count', type=int, default=3)
    parser.add_argument('--cases-per-seed', type=int, default=21)
    parser.add_argument('--out', type=Path, help='Fresh JSON report; otherwise emit JSON to stdout')
    parser.add_argument('--case-csv', type=Path, help='Optional fresh per-entry CSV, including every round')
    args = parser.parse_args()
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        kernel.SetProcessAffinityMask.argtypes = (ctypes.c_void_p, ctypes.c_size_t)
        if not kernel.SetProcessAffinityMask(kernel.GetCurrentProcess(), 0x30000000):
            raise ctypes.WinError(ctypes.get_last_error())
    for path in (args.out, args.case_csv):
        if path is not None and path.exists():
            parser.error(f'Fresh output required: {path}')
    report = analyze(args.root, worker_mib=args.worker_memory_mib, reserve_mib=args.reserve_mib,
                     rounds=args.rounds, seed_count=args.seed_count, cases_per_seed=args.cases_per_seed)
    if args.case_csv:
        fields = ('round', 'seed', 'case', 'repeat', 'room', 'floor', 'candidate_over_baseline')
        metric_fields = [f'{arm}_{metric}' for arm in ('baseline', 'candidate') for metric in METRICS]
        with args.case_csv.open('x', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=[*fields, *metric_fields])
            writer.writeheader()
            for row in report['case_pairs']:
                flat = {k: row[k] for k in fields}
                flat.update({f'{arm}_{metric}': row[arm][metric] for arm in ('baseline', 'candidate') for metric in METRICS})
                writer.writerow(flat)
    output = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    if args.out:
        with args.out.open('x', encoding='utf-8') as stream:
            stream.write(output + '\n')
        print(json.dumps({k: report[k] for k in ('authoritative_state', 'timing_measurement_valid', 'step_timing_passed', 'whole_b1_timing_passed')}))
    else:
        print(output)


if __name__ == '__main__':
    main()
