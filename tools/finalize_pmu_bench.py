"""Finish fixed-workload capture evidence with retained full-byte comparison and provenance."""
import argparse
import ctypes
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.fight_bench import GAME_FIELDS, _canonical
from spire_exact.planning.io import write_json


def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('folder', type=Path); a = p.parse_args()
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True); kernel.GetCurrentProcess.restype = ctypes.c_void_p
        kernel.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        if not kernel.SetProcessAffinityMask(kernel.GetCurrentProcess(), (1 << 28) | (1 << 29)): raise ctypes.WinError()
    folder = a.folder.resolve()
    state = json.loads((folder / 'pmu-status.json').read_text())
    if state['state'] != 'capture_completed': raise ValueError('Require all four captures and strict request check')
    summary = json.loads((folder / 'pmu-summary.json').read_text())
    reference, checks, roles = None, [], {}
    reference_gc = None
    for r in summary['rounds']:
        name = r['round']; run = folder / name
        if not r['time_audit']['all_pass']: raise ValueError('Clock audit must pass')
        pmc = json.loads((folder / (name + '-pmc.json')).read_text())
        if not pmc['valid'] or pmc['ambiguous_worker_intervals']: raise ValueError('Worker PMU attribution must be complete')
        groups = {}
        for t in pmc['threads']:
            group = '|'.join(sorted(t['names'])) or '(unnamed)'
            out = groups.setdefault(group, {'on_cpu_seconds': 0, 'counters': {k: 0 for k in pmc['counter_names']}, 'threads': 0})
            out['threads'] += 1; out['on_cpu_seconds'] += t['metrics']['on_cpu_seconds']
            for k, v in t['metrics']['counters'].items(): out['counters'][k] += v
        for g in groups.values():
            g['ipc'] = g['counters']['InstructionRetired'] / g['counters']['UnhaltedCoreCycles']
            g['llc_misses_per_node'] = g['counters']['LLCMisses'] / r['nodes']
        roles[name] = groups
        resource = json.loads((folder / (name + '-resources.json')).read_text())
        if not resource['enforced'] or resource['workload_limit_bytes'] != 24576 * 1048576:
            raise ValueError('Job memory override was not actually enforced')
        for repeat in range(r['requests']):
            with gzip.open(run / f'case-0000-r{repeat}/data/decision.json.gz', 'rt', encoding='utf-8-sig') as stream:
                decision = json.load(stream)
            value = _canonical({k: decision.get(k) for k in GAME_FIELDS})
            if reference is None: reference = value
            gc = decision['performance']['gc_configuration']
            if reference_gc is None: reference_gc = gc
            equal = value == reference  # actual retained canonical bytes, not merely digest equality
            checks.append({'round': name, 'repeat': repeat, 'game_bytes_equal_reference': equal,
                           'gc_configuration_equal': gc == reference_gc})
            if not equal or gc != reference_gc or gc['GCHeapHardLimit'] != 768 * 1048576:
                raise ValueError('Full game bytes or actual GC settings differ')
    parser = ROOT / 'tools/pmu_analyzer/bin/Release/net9.0/PmuAnalyzer.dll'
    provenance = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in (parser, ROOT / 'tools/pmu_analyzer/Program.cs', ROOT / 'tools/pmu_analyzer/Aggregate.cs',
                               ROOT / 'tools/analyze_pmu_bench.py')}
    report = {'schema': 'spire-pmu-final-audit/v1', 'all_retained_game_bytes_equal': True, 'requests': len(checks),
              'game_fields': GAME_FIELDS, 'canonical_reference_bytes': len(reference),
              'all_gc_configuration_equal': True, 'gc_configuration': reference_gc, 'checks': checks,
              'parser_provenance': provenance, 'scope': 'full retained game export comparison; not native-state identity or campaign validation'}
    write_json(folder / 'native-equivalence-report.json', report)
    write_json(folder / 'thread-role-summary.json', roles)
    document = ROOT / 'experiments/iteration-053'
    write_json(document / 'pmu-summary.json', summary); write_json(document / 'native-equivalence-report.json', report)
    write_json(document / 'thread-role-summary.json', roles)
    state.update(state='completed', analysis_pending=False, analysis_completed_unix=time.time(),
                 all_pmu_valid=True, all_retained_game_bytes_equal=True, requests=report['requests'],
                 report=str(document / 'results.md'))
    write_json(folder / 'pmu-status.json', state)
    print(json.dumps({'state': 'completed', 'requests': len(checks), 'full_game_bytes_equal': True, 'report': state['report']}))


if __name__ == '__main__': main()
