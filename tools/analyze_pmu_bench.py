"""Resolve measured worker identities and decode actual CSwitch PMC captures on E cores."""
import argparse
import ctypes
import gzip
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from spire_exact.planning.io import write_json


def analyze(folder, name, reparse=False):
    run = folder / name
    manifest = json.loads((run / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('pmu_stop_exit') != 0: raise ValueError('Capture must be stopped successfully before analysis')
    rows = [json.loads(line) for line in (run / 'results.jsonl').read_text().splitlines()]
    worker_pids, gc_counts, audits, allocated, full_allocated = set(), [0, 0, 0], [], 0, 0
    transports = []
    for r in rows:
        if r.get('error'): raise ValueError('A measured request failed')
        case = run / f'case-0000-r{r["repeat"]}'
        tx = json.loads((case / 'transport.json').read_text()); worker_pids.add(tx['pid']); transports.append(tx)
        if tx.get('recycle_reason'): raise ValueError('Worker recycled during measurement')
        with gzip.open(case / 'data/decision.json.gz', 'rt', encoding='utf-8-sig') as stream: decision = json.load(stream)
        perf = decision.get('performance') or {}
        full_allocated += perf.get('allocated_bytes', 0)
        for n, value in enumerate(perf.get('collections', [])): gc_counts[n] += value
        for search in (decision.get('advisor_metrics') or {}).get('searches', []):
            t = search.get('wall_us', 0) / 1000; budget = search.get('budget_ms')
            audits.append({'repeat': r['repeat'], 'budget_ms': budget, 'search_ms': t,
                           'pass': budget is not None and 9 * (t + 1) < budget and not search.get('time_boundary')})
            allocated += search.get('allocated_bytes', 0)
    if len(worker_pids) != manifest['resources']['workers']: raise ValueError('All requested workers must have measured work')
    if min(manifest['warm_worker_jobs']) < 4: raise ValueError('Every worker must complete four warmup requests')
    config = {'worker_pids': sorted(worker_pids), 'begin_unix': manifest['measured_started_unix'],
              'end_unix': manifest['measured_finished_unix']}
    config_file = folder / (name + '-pmc-config.json'); write_json(config_file, config)
    output = folder / (name + '-pmc.json')
    if reparse or not output.exists():
        dotnet = ROOT.parent / '.tools/dotnet/dotnet.exe'
        command = [str(dotnet), str(ROOT / 'tools/pmu_analyzer/bin/Release/net9.0/PmuAnalyzer.dll'),
                   '--aggregate', manifest['pmu_trace'], str(config_file), str(output)]
        done = subprocess.run(command, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        (folder / (name + '-pmc-analysis.log')).write_text(done.stdout + done.stderr, encoding='utf-8')
        if done.returncode: raise RuntimeError(f'PMU aggregation failed for {name}: {done.stdout} {done.stderr}')
    pmc = json.loads(output.read_text())
    if not pmc['valid']: raise ValueError('Actual PMU validation failed')
    wall = config['end_unix'] - config['begin_unix']; nodes = sum(r['nodes'] for r in rows)
    samples = [json.loads(line) for line in (folder / (name + '-system.jsonl')).read_text().splitlines()]
    samples = [r for r in samples if config['begin_unix'] <= r['unix'] <= config['end_unix']]
    values = {}
    for r in samples:
        for key, value in r.get('values', {}).items():
            if value.get('value') is not None: values.setdefault(key, []).append(value['value'])
    system = {key: {'median': statistics.median(v), 'min': min(v), 'max': max(v), 'samples': len(v)} for key, v in values.items()}
    totals = pmc['total']; counters = totals['counters']
    return {'round': name, 'workers': manifest['resources']['workers'], 'logical_cpus': manifest['affinity']['cpus'],
            'requests': len(rows), 'nodes': nodes, 'measurement_wall_seconds': wall, 'nodes_per_wall_second': nodes / wall,
            'sum_search_seconds': sum(r['search_wall'] for r in rows), 'sum_service_seconds': sum(tx['wall_seconds'] for tx in transports),
            'gc_pause_seconds': sum(r['gc_pause'] for r in rows), 'collections': gc_counts,
            'search_allocated_bytes': allocated, 'full_task_allocated_bytes': full_allocated,
            'pmc': totals, 'instructions_per_node': counters['InstructionRetired'] / nodes,
            'core_cycles_per_node': counters['UnhaltedCoreCycles'] / nodes,
            'llc_misses_per_node': counters['LLCMisses'] / nodes,
            'on_cpu_seconds_per_node': totals['on_cpu_seconds'] / nodes,
            'hardware_events_lost': pmc['EventsLost'], 'counter_monotonicity_failures': pmc['decreases'],
            'metadata_valid': pmc['metadataValid'], 'time_audit': {'all_pass': all(r['pass'] for r in audits), 'searches': len(audits),
              'minimum_margin_ratio': min(r['budget_ms'] / (9 * (r['search_ms'] + 1)) for r in audits)},
            'system_pdh': system, 'all_warm_worker_jobs': manifest['warm_worker_jobs'],
            'clock_audit_rows': audits, 'host_sha256': manifest['host_sha256']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path); parser.add_argument('--rounds', nargs='+', default=['p7-a', 'p14-b', 'p14-c', 'p7-d'])
    parser.add_argument('--reparse', action='store_true')
    args = parser.parse_args()
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True); kernel.GetCurrentProcess.restype = ctypes.c_void_p
        kernel.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        if not kernel.SetProcessAffinityMask(kernel.GetCurrentProcess(), (1 << 28) | (1 << 29)): raise ctypes.WinError()
    rows = [analyze(args.folder.resolve(), name, args.reparse) for name in args.rounds]
    comparisons = []
    for left, right in [('p7-a', 'p14-b'), ('p7-d', 'p14-c-retry' if 'p14-c-retry' in args.rounds else 'p14-c')]:
        by_name = {r['round']: r for r in rows}
        if left not in by_name or right not in by_name: continue
        a, b = by_name[left], by_name[right]
        comparisons.append({'p7': left, 'p14': right,
          'throughput_ratio_14_over_7': b['nodes_per_wall_second'] / a['nodes_per_wall_second'],
          'mean_search_service_ratio_14_over_7': b['sum_search_seconds'] / a['sum_search_seconds'],
          'instructions_per_node_ratio': b['instructions_per_node'] / a['instructions_per_node'],
          'cycles_per_node_ratio': b['core_cycles_per_node'] / a['core_cycles_per_node'],
          'ipc_ratio': b['pmc']['ipc'] / a['pmc']['ipc'],
          'llc_misses_per_node_ratio': b['llc_misses_per_node'] / a['llc_misses_per_node'],
          'mpki_ratio': b['pmc']['llc_mpki'] / a['pmc']['llc_mpki'],
          'cycles_per_on_cpu_second_ratio': b['pmc']['cycles_per_on_cpu_second'] / a['pmc']['cycles_per_on_cpu_second']})
    result = {'schema': 'spire-smt-pmu-bench/v1', 'rounds': rows, 'comparisons': comparisons,
      'scope': 'Single retained seed-101 ordinary fight, repeated fixed node work. Two instrumented rounds per worker count. No campaign/general scaling conclusion.',
      'caveats': ['CSwitch interval counts include runtime/GC and kernel interrupts while that thread is scheduled.',
                  'PDH Processor Performance is a frequency proxy relative to nominal performance, not calibrated GHz.',
                  'LLC references/misses do not measure DRAM bytes or bandwidth saturation.',
                  'Counter ordering is decoded from capture metadata, not inferred from XML profile order.']}
    write_json(args.folder / ('pmu-summary.json' if len(rows) == 4 else 'pmu-partial-summary.json'), result)
    for r in rows: print(json.dumps({k: r[k] for k in ('round', 'nodes', 'measurement_wall_seconds', 'instructions_per_node', 'core_cycles_per_node', 'llc_misses_per_node', 'pmc', 'time_audit')}, ensure_ascii=False))
    for r in comparisons: print(json.dumps(r))


if __name__ == '__main__': main()
