"""Read-only menu grouping comparison for one explicitly selected old run.

Only macro_forge and historical macro_card_skip attempts and their named
parent requests/decision evidence are read. This neither tests nor runs a
solver. Legacy coordinates recovered from prior completed map actions are
labelled inferred_proxy: they are never runtime state-equivalence evidence.
"""
from collections import Counter
from functools import lru_cache
from pathlib import Path
import argparse
import datetime
import hashlib
import json
import os
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from spire_exact.canonical import canonical
from spire_exact.planning.preparation import coord, forge_menu_key
from tools.preparation_review import inside, read_object, decision_for

KINDS = ('macro_forge', 'macro_card_skip')


def pin_e_cores():
    """Apply and verify this process's E24-29 mask before reading run data."""
    if os.name != 'nt':
        raise RuntimeError('this authorized analysis requires Windows E-core affinity')
    import ctypes as c
    from ctypes import wintypes as w
    from tools.cpu_topology import inventory
    rows = inventory()
    cpus = list(range(24, 30))
    classes = {row['efficiency_class'] for row in rows}
    selected = [row for row in rows if row['group'] == 0 and row['logical_cpu'] in cpus]
    if (len(classes) < 2 or len(selected) != len(cpus)
            or any(row['efficiency_class'] != min(classes) for row in selected)):
        raise RuntimeError('requested E24-29 CPUs are not verified low-efficiency CPU sets')
    kernel = c.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.SetProcessAffinityMask.argtypes = [w.HANDLE, c.c_size_t]
    kernel.SetProcessAffinityMask.restype = w.BOOL
    kernel.GetProcessAffinityMask.argtypes = [w.HANDLE, c.POINTER(c.c_size_t), c.POINTER(c.c_size_t)]
    kernel.GetProcessAffinityMask.restype = w.BOOL
    kernel.SetPriorityClass.argtypes = [w.HANDLE, w.DWORD]
    kernel.SetPriorityClass.restype = w.BOOL
    process, mask = kernel.GetCurrentProcess(), sum(1 << cpu for cpu in cpus)
    if not kernel.SetProcessAffinityMask(process, mask) or not kernel.SetPriorityClass(process, 0x00004000):
        raise c.WinError(c.get_last_error())
    actual, system = c.c_size_t(), c.c_size_t()
    if not kernel.GetProcessAffinityMask(process, c.byref(actual), c.byref(system)) or actual.value != mask:
        raise RuntimeError('analysis affinity verification failed')
    return {'logical_cpus': cpus, 'efficiency_class': min(classes), 'affinity_mask': hex(mask),
            'priority': 'below_normal', 'source': 'GetSystemCpuSetInformation + GetProcessAffinityMask'}


def default_run():
    # Look in this worktree's iteration first, then its recorded queue/junction.
    direct = ROOT / 'experiments/iteration-075/final30b-524130501'
    if direct.is_dir():
        return direct
    queue = read_object_queue(ROOT / 'experiments/iteration-075/final-gzip-queue.json')
    if len(queue) != 1:
        raise ValueError('expected the single final-gzip source job')
    job = queue[0]
    name, iteration = (job[job.index(key) + 1] for key in ('--name', '--iteration'))
    if name != 'final30b-524130501':
        raise ValueError('source queue is not the authorized final30b run')
    candidate = ROOT / 'experiments' / (iteration + '-runs') / name
    if candidate.is_dir():
        return candidate
    raise FileNotFoundError('final30b not found in this worktree iteration/junction; pass one explicit run')


def read_object_queue(path):
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value, list) or any(not isinstance(job, list) for job in value):
        raise ValueError('invalid source queue')
    return value


def request_for(seed_dir, label, run):
    folder = inside(seed_dir / label, run)
    path = folder / 'request.json'
    if path.is_file():
        return read_object(path), str(path)
    path = folder / 'cached.json'
    document = read_object(path)
    request = document.get('request')
    if not isinstance(request, dict):
        raise ValueError('missing_cached_request')
    return request, str(path)


def native_or_inferred_coord(source, index, evidence):
    obs = evidence['observation']
    explicit = [obs[key] for key in ('map_coord', 'current_coord') if key in obs]
    if explicit:
        values = [coord(value) for value in explicit]
        if len(set(values)) != 1:
            raise ValueError('conflicting_explicit_coordinate')
        return explicit[0], 'native_observation', None
    rows = [row for row in source.get('preparation_menu_sources', []) if isinstance(row, dict)
            and type(row.get('index')) is int and row['index'] == index]
    if rows:
        if (len(rows) != 1 or rows[0].get('phase') != evidence.get('phase')
                or canonical(rows[0].get('observation')) != canonical(obs)):
            raise ValueError('misbound_native_coordinate')
        coord(rows[0].get('map_coord'))
        return rows[0]['map_coord'], 'native_side_table', None
    # The later observed menu establishes that these earlier actions were
    # executed. Their coordinates are still an OFFLINE proxy: events or other
    # native behavior could have moved the player without a map action.
    trace, prior_evidence, act = source['trace'], source['decision_evidence'], obs.get('act')
    for previous in range(index - 1, -1, -1):
        row, action = prior_evidence[previous], trace[previous]
        prior_obs = row.get('observation') or {}
        if type(prior_obs.get('act')) is int and prior_obs['act'] != act:
            return None, 'unknown', None
        if (prior_obs.get('act') == act and row.get('phase') in ('map', 'shop')
                and action.get('kind') == 'map'):
            if not any(canonical(action) == canonical(legal) for legal in row.get('available_actions', [])):
                raise ValueError('inferred_map_action_not_in_native_menu')
            col, row_number = coord(action)
            return {'col': col, 'row': row_number}, 'inferred_proxy', previous
    return None, 'unknown', None


def comparison(samples):
    report = {}
    for mode in ('exact', 'threshold'):
        grouped = Counter(sample['keys'][mode] for sample in samples if sample['keys'][mode] is not None)
        known = sum(grouped.values())
        members = {}
        for sample in samples:
            key = sample['keys'][mode]
            if key is not None:
                members.setdefault(key, []).append(sample['label'])
        examples = sorted(grouped, key=lambda key: (-grouped[key], key))[:3]
        report[mode] = {'records': len(samples), 'key_known': known, 'key_missing': len(samples) - known,
                        'unique_groups': len(grouped), 'mergeable_repeated_proposals': known - len(grouped),
                        'largest_group': max(grouped.values(), default=0),
                        'examples': [{'key_sha256_label_only': hashlib.sha256(key).hexdigest(),
                                      'count': grouped[key], 'first_labels': members[key][:8]} for key in examples]}
    return report


def inspect(run):
    run = Path(run).resolve()
    manifest = read_object(run / 'validation-manifest.json')
    seed = str(manifest.get('seed'))
    if seed != '524130501' or manifest.get('run_id') != 'final30b-524130501':
        raise ValueError('select only the authorized final30b-524130501 run')
    seed_dir = inside(run / ('seed-' + seed), run)
    ledger = seed_dir / 'evaluations.jsonl'
    records, ledger_issues = [], []
    with ledger.open(encoding='utf-8-sig') as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError('non_object')
                records.append(row)
            except ValueError:
                ledger_issues.append({'line': line_number, 'reason': 'invalid_or_incomplete_ledger_line'})
    by_label = {row['label']: row for row in records if isinstance(row.get('label'), str)}
    selected = [row for row in records if row.get('kind') in KINDS]
    rejections, samples, rejected = Counter(), [], []

    @lru_cache(maxsize=4)
    def source_for(label):
        if label not in by_label:
            raise ValueError('parent_not_in_same_run_ledger')
        request, request_path = request_for(seed_dir, label, run)
        document, decision_path = decision_for(seed_dir, label, run)
        if document.get('synthetic') is True:
            raise ValueError('synthetic_parent')
        return request, document, request_path, decision_path

    for record in selected:
        label = record.get('label')
        try:
            prep = record.get('preparation') or {}
            parent, index = prep.get('source'), prep.get('index')
            if not isinstance(parent, str) or type(index) is not int or index < 0:
                raise ValueError('missing_parent_menu_reference')
            request, request_path = request_for(seed_dir, label, run)
            parent_request, source, parent_request_path, decision_path = source_for(parent)
            for key in ('seed', 'character', 'ascension', 'unlocks'):
                expected = manifest.get(key) if key in manifest else {'character': 'IRONCLAD', 'ascension': 10, 'unlocks': 'all'}[key]
                if request.get(key) != expected or parent_request.get(key) != expected:
                    raise ValueError('request_context_mismatch:' + key)
            history, trace, evidence = request.get('history'), source.get('trace'), source.get('decision_evidence')
            if (not isinstance(history, list) or not isinstance(trace, list) or not isinstance(evidence, list)
                    or len(history) != index + 1 or record.get('prefix_length') != len(history)
                    or index >= len(trace) or index >= len(evidence)
                    or canonical(history[:-1]) != canonical(trace[:index])):
                raise ValueError('parent_prefix_mismatch')
            menu, action = evidence[index], history[-1]
            if not isinstance(menu, dict) or not isinstance(menu.get('observation'), dict):
                raise ValueError('missing_native_menu_observation')
            actions = menu.get('available_actions')
            if (not isinstance(actions, list) or not any(canonical(action) == canonical(legal) for legal in actions)
                    or not any(canonical(trace[index]) == canonical(legal) for legal in actions)
                    or canonical(action) == canonical(trace[index])):
                raise ValueError('alternative_action_not_bound_to_native_menu')
            if menu['observation'].get('act') != prep.get('act'):
                raise ValueError('parent_menu_act_mismatch')
            if record['kind'] == 'macro_forge':
                if menu.get('phase') != 'rest' or action.get('kind') != 'rest' or action.get('option') != 'SMITH':
                    raise ValueError('not_actual_smith_proposal')
            elif menu.get('phase') != 'card_reward' or action.get('kind') != 'card_skip':
                raise ValueError('not_actual_historical_skip_proposal')
            coordinate, origin, map_index = native_or_inferred_coord(source, index, menu)
            keys = {mode: forge_menu_key(menu, mode, map_coord=coordinate, hp_percent=50)
                    for mode in ('exact', 'threshold')}
            samples.append({'label': label, 'kind': record['kind'], 'parent': parent, 'index': index,
                'prefix_bound': True, 'alternative_action_bound': True, 'coordinate': coordinate,
                'coordinate_origin': origin, 'inferred_map_action_index': map_index,
                'act': menu['observation'].get('act'), 'floor': menu['observation'].get('floor'),
                'hp': menu['observation'].get('hp'), 'max_hp': menu['observation'].get('max_hp'),
                'request': request_path, 'parent_request': parent_request_path, 'parent_decision': decision_path,
                'keys': keys})
        except (OSError, ValueError, TypeError, KeyError, IndexError) as error:
            rejections[str(error)] += 1
            rejected.append({'label': label, 'kind': record.get('kind'), 'reason': str(error)})
    sections = {}
    for kind in KINDS:
        rows = [sample for sample in samples if sample['kind'] == kind]
        native = [sample for sample in rows if sample['coordinate_origin'].startswith('native_')]
        sections[kind] = {'ledger_attempts': sum(row.get('kind') == kind for row in selected),
            'prefix_action_bound': len(rows), 'binding_rejected_or_missing': sum(row['kind'] == kind for row in rejected),
            'coordinate_origins': dict(Counter(sample['coordinate_origin'] for sample in rows)),
            'offline_comparison_including_inferred_proxy': comparison(rows),
            'runtime_usable_native_coordinate_only': comparison(native)}
    return {'schema': 'spire-preparation-dedup-review/v1', 'iteration': 'iteration-080',
        'generated': datetime.datetime.now().astimezone().isoformat(), 'run': str(run), 'seed': seed,
        'source_version': manifest.get('version'), 'evaluations': str(ledger), 'ledger_records': len(records),
        'selected_attempts': len(selected), 'prefix_action_bound': len(samples),
        'binding_rejections': dict(rejections), 'rejected_samples': rejected, 'ledger_issues': ledger_issues,
        'hp_percent': 50, 'max_hp_preserved_in_both_modes': True,
        'by_kind': sections, 'samples': [{key: value for key, value in sample.items() if key != 'keys'} for sample in samples],
        'comparison_identity': 'full canonical menu-key bytes; hashes in examples are labels only',
        'scope': 'one old run, descriptive grouping only; no tuning, state equality, pruning, runtime gain or win claim',
        'coordinate_limit': 'inferred_proxy uses a prior executed same-act legal map action, not a native current-coordinate snapshot; '
                            'it is unavailable to runtime grouping and cannot establish state equivalence',
        'historical_skip_scope': 'macro_card_skip is removed from production; its groups are historical diagnostics only'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', nargs='?', type=Path)
    parser.add_argument('--out', type=Path, default=ROOT / 'experiments/iteration-080/forge-menu-dedup.json')
    args = parser.parse_args()
    output = args.out.resolve()
    if output.exists():
        raise FileExistsError('report already exists; never overwrite an earlier analysis')
    affinity = pin_e_cores()
    started = time.perf_counter()
    report = inspect(args.run or default_run())
    report['analysis_process'] = affinity | {'pid': os.getpid(), 'wall_seconds': time.perf_counter() - started}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'report': str(output), 'selected_attempts': report['selected_attempts'],
                      'prefix_action_bound': report['prefix_action_bound'],
                      'by_kind': {kind: data['offline_comparison_including_inferred_proxy']
                                  for kind, data in report['by_kind'].items()}, 'analysis_process': report['analysis_process']}))


if __name__ == '__main__':
    main()
