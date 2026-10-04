"""Read retained EventPipe/Speedscope profiles; no new native workload or trace.

Sample time is conditioned on SolveCore. Snapshot descendants form a disjoint
partition; native/inlined work stays at its last resolved caller. Allocation
interval bytes are estimates from the existing allocation-tick decoder.
"""
import argparse
from collections import Counter
import ctypes
import gc
import hashlib
import json
import os
from pathlib import Path
import time


METHOD_CATEGORIES = {
    'state_key': ['.BuildStateKey(', '.GetFingerprint(', '.AppendStateFingerprint('],
    'pile_card_fingerprints': ['.BuildUnorderedPileKey(', '.BuildCyclePileShapeKey(',
        '.BuildCardStateFingerprint(', '.CaptureCardStateFingerprintForTesting(', '.AppendPile(', '.AppendUnorderedPile('],
    'projected_shuffle': ['.BuildProjectedShuffleOrder('],
    'threat_projection': ['.ProjectHpAfterThreat('],
    'coverage': ['.GetCoverageSummary('],
    'relic_evaluation': ['.EvaluateRelicCounters('],
    'snapshot_score_helpers': ['.EstimateLatentAttackValue(', '.EstimateReplayValue(',
        '.EstimateSummonValue(', '.GetEffectiveCardEnergyCost(', '.CalculateCardCostForSnapshot(',
        '.GetAttackCardValue(', '.GetStatefulPowerShape(', '.BuildPotionInventoryKey('],
    'nested_fork': ['CombatPredictionSimulator.Fork('],
}


def analyze_profile(document):
    names = [frame['name'] for frame in document['shared']['frames']]
    frame_category = []
    for name in names:
        category = next((key for key, patterns in METHOD_CATEGORIES.items()
                         if any(pattern in name for pattern in patterns)), None)
        frame_category.append(category)
    solve_flags = ['CombatBeamSolver.SolveCore(' in name for name in names]
    snapshot_flags = ['CombatBeamSolver.Snapshot(' in name for name in names]
    solver_flags = [name.startswith('CombatSolver!') for name in names]
    partitions, leaves, callers = Counter(), Counter(), Counter()
    total = snapshot = 0.0
    threads = []
    for profile in document['profiles']:
        if profile.get('type') != 'evented':
            continue
        factor = {'milliseconds': 1.0, 'seconds': 1000.0,
                  'microseconds': .001, 'nanoseconds': .000001}[profile['unit']]
        stack, categories, solvers = [], [], []
        solve_depth = snapshot_depth = 0
        previous = profile.get('startValue', 0)
        thread_total = thread_snapshot = 0.0
        for event in profile['events']:
            dt = (event['at']-previous)*factor
            if dt < 0:
                raise ValueError('Nonmonotonic profile')
            if dt and solve_depth:
                total += dt
                thread_total += dt
                if snapshot_depth:
                    snapshot += dt
                    thread_snapshot += dt
                    partitions[categories[-1] if categories else 'snapshot_body_or_inlined'] += dt
                    leaves[names[stack[-1]]] += dt
                    callers[names[solvers[-1]] if solvers else 'unresolved_solver_caller'] += dt
            frame = event['frame']
            if event['type'] == 'O':
                stack.append(frame)
                solve_depth += solve_flags[frame]
                snapshot_depth += snapshot_flags[frame]
                if frame_category[frame]:
                    categories.append(frame_category[frame])
                if solver_flags[frame]:
                    solvers.append(frame)
            elif event['type'] == 'C':
                if not stack or stack.pop() != frame:
                    raise ValueError('Unbalanced stack')
                solve_depth -= solve_flags[frame]
                snapshot_depth -= snapshot_flags[frame]
                if frame_category[frame]:
                    categories.pop()
                if solver_flags[frame]:
                    solvers.pop()
            else:
                raise ValueError('Unknown event')
            previous = event['at']
        if stack or categories or solvers:
            raise ValueError('Unclosed stack')
        threads.append({'name': profile.get('name'), 'search_sample_ms': thread_total,
                        'snapshot_sample_ms': thread_snapshot})
    def ranked(counter):
        return [{'name': name, 'sample_ms': value, 'search_percent': value/total*100,
                 'snapshot_percent': value/snapshot*100} for name,value in counter.most_common()]
    return {'search_sample_ms': total, 'snapshot_sample_ms': snapshot,
            'snapshot_search_percent': snapshot/total*100,
            'disjoint_snapshot_categories': ranked(partitions),
            'snapshot_exclusive_leaves': ranked(leaves),
            'snapshot_nearest_solver_callers': ranked(callers), 'threads': threads,
            'partition_residual_ms': snapshot-sum(partitions.values())}


def allocations(document):
    total = document['search_allocation_interval_bytes']
    snapshot = next(row['interval_bytes'] for row in document['by_stage'] if row['name']=='Snapshot')
    grouped = {}
    for row in document['by_second_stage_type']:
        _second, stage, name = row['name'].split('|', 2)
        if stage != 'Snapshot':
            continue
        stat = grouped.setdefault(name, {'name':name, 'ticks':0, 'interval_bytes':0, 'sampled_object_bytes':0})
        for key in ('ticks','interval_bytes','sampled_object_bytes'):
            stat[key] += row[key]
    rows = sorted(grouped.values(),key=lambda row:row['interval_bytes'],reverse=True)
    return {'search_interval_bytes': total, 'snapshot_interval_bytes': snapshot,
            'snapshot_allocation_percent': snapshot/total*100, 'snapshot_type_rows': rows,
            'nearest_snapshot_frames': [row for row in document['by_nearest_solver_frame']
                if '.Snapshot(' in row['name'] or any(pattern in row['name']
                    for patterns in METHOD_CATEGORIES.values() for pattern in patterns)],
            'events_lost': document['events_lost'],
            'no_stack_ticks': document['no_stack_ticks'],
            'truncated_stacks': document['truncated_stacks']}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--profiles', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        kernel.SetProcessAffinityMask.argtypes = (ctypes.c_void_p, ctypes.c_size_t)
        kernel.SetPriorityClass.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
        kernel.SetProcessAffinityMask(kernel.GetCurrentProcess(), 0x03000000)
        kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x4000)  # BELOW_NORMAL
    cpu_reports, allocation_reports = [], []
    started = time.time()
    for seed in ('10101010','1741222413','564940356'):
        path = a.profiles/seed/'warm.speedscope.json'
        raw = path.read_bytes()
        report = analyze_profile(json.loads(raw))
        report.update(seed=seed, source=str(path), source_sha256=hashlib.sha256(raw).hexdigest())
        cpu_reports.append(report)
        del raw
        gc.collect()
        path = a.profiles/seed/'warm-allocations.json'
        raw = path.read_bytes()
        report = allocations(json.loads(raw))
        report.update(seed=seed, source=str(path), source_sha256=hashlib.sha256(raw).hexdigest())
        allocation_reports.append(report)
        del raw
        gc.collect()
        print(json.dumps({'seed':seed,'state':'analyzed','snapshot_percent':cpu_reports[-1]['snapshot_search_percent']}),flush=True)
    common = {'source_kind':'existing retained P7 warm profile', 'new_native_workloads':0,
              'analysis_affinity':'0x03000000 E24/E25', 'elapsed_seconds':time.time()-started,
              'scope':'Sampled thread time and estimated allocation intervals, not new performance or optimization validation'}
    a.out.mkdir(parents=True,exist_ok=True)
    for filename,reports in [('snapshot-costs.json',cpu_reports),('allocation-summary.json',allocation_reports)]:
        path = a.out/filename
        with path.open('x',encoding='utf-8') as stream:
            json.dump({**common,'reports':reports},stream,ensure_ascii=False,indent=2)
            stream.write('\n')


if __name__ == '__main__':
    main()
