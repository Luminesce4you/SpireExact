"""Recorded-control library (PLAN-v2 R0 / R2a): one row per recorded run.

A recorded run of the default configuration can stand in for a control arm
without being run again (PLAN-v2 1.1: real reruns of one configuration and
solver_seed are identical until an infrastructure failure). This tool lists
every recording with what decides whether it may serve as a control:

- identity: planner version, host / game / CombatSolver build, native runtime,
  worker count and the planner's Python version (3.12 float sum() differs). A recording is a control only for a treatment run on the
  same `host_key` (a CombatSolver upgrade is a new host: its beam results
  differ, so no older recording is its control; user 10-12).
- the golden replay (tools/replay_golden.py --tag TAG): the refactored planner
  reproduces the recording request for request;
- infrastructure failures (RESOURCE_LIMIT, NATIVE_CRASH, TIMEOUT, UNKNOWN) at
  or before the last compared serial: the trajectory then depends on the
  machine, not only on the configuration, so the run is listed but not a control;
- first-win time, stop reason, role (A = solver_seed 271828 compares,
  A2 = 314159 stratifies; PLAN-v2 R2a point 5), retained checkpoints.

Read-only: only result.json, evaluations.jsonl, the manifest and one
identity.json per run are read, plus a directory listing for checkpoints.

  python tools/control_library.py --out PATH [--replay-tag TAG] [--checkpoints] [RUN_DIR ...]

Without RUN_DIRs: the 104 r20b and 105 runs (all groups; other groups are
listed with their own version so that they are never mistaken for i054a).
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from replay_golden import ART, REPORTS, canonical, now, pin, read, recording, serial_of, write  # noqa: E402

SCHEMA = 'spire-control-library/v1'
# Planner interpreter of the runs whose manifest does not record it (the
# iteration-104 / 105 launchers; see replay_golden.RECORDING_PYTHON).
UNRECORDED_PYTHON = '3.12.14'
INFRA = ('RESOURCE_LIMIT', 'NATIVE_CRASH', 'TIMEOUT', 'UNKNOWN')
ROLES = {271828: 'compare', 314159: 'stratify'}
DEFAULT_GLOBS = ('iteration-104-i054a-r20b/i054a-shm-r20b-*', 'iteration-105-ablation/ab105-*')


def sha(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def combat_solver(identity):
    """'CombatSolver 4d2c550; ...' -> '4d2c550' (None when not stated)."""
    text = identity.get('bootstrap') or ''
    head = text.split(';', 1)[0].split()
    return head[1] if len(head) >= 2 and head[0] == 'CombatSolver' else None


def group_of(name):
    """ab105-A2-36219659 -> ab105-A2; i054a-shm-r20b-365003757 -> i054a-shm-r20b."""
    head, _, tail = name.rpartition('-')
    return head if tail.isdigit() else name


def checkpoints(seed_dir, ledger):
    """Evaluations whose data/checkpoints directory still holds files."""
    kept = 0
    for row in ledger:
        folder = seed_dir / row['label'] / 'data' / 'checkpoints'
        try:
            with os.scandir(folder) as it:
                kept += any(True for _ in it)
        except OSError:
            pass
    return kept


def row_of(run, replay_tag, scan_checkpoints):
    manifest, seed_dir, original, ledger, winner = recording(run)
    first = ledger[0]['label']
    identity = read(seed_dir / first / 'data' / 'identity.json')
    stop = original.get('stop_reason')
    last = winner if winner is not None else (max(serial_of(r['label']) for r in ledger) if ledger else None)
    by_class = {}
    infra = []
    for r in ledger:
        c = r.get('classification') or 'NONE'
        by_class[c] = by_class.get(c, 0) + 1
        if c in INFRA and last is not None and serial_of(r['label']) <= last:
            infra.append({'serial': serial_of(r['label']), 'classification': c, 'reason': r.get('reason')})
    win_row = next((r for r in ledger if winner is not None and serial_of(r['label']) == winner), None)
    host = {'host_sha256': identity.get('host_sha256'), 'game_sha256': identity.get('game_sha256'),
            'combat_solver': combat_solver(identity), 'native_runtime': original.get('native_runtime'),
            'workers': (original.get('resources') or {}).get('workers'),
            'planner_python': manifest.get('python') or UNRECORDED_PYTHON}
    solver_seed = manifest.get('solver_seed')
    row = {
        'run': run.name, 'path': str(run), 'group': group_of(run.name),
        'seed': str(manifest['seed']), 'solver_seed': solver_seed, 'role': ROLES.get(solver_seed, 'other'),
        'version': manifest.get('version'), 'workspace': manifest.get('workspace'),
        'host': host, 'host_key': sha(host)[:16],
        'configuration_sha256': sha(original.get('configuration')),
        'stop_reason': stop, 'wall_cap_seconds': manifest.get('wall_cap_seconds'),
        'elapsed_seconds': original.get('elapsed_seconds'),
        'winning_serial': winner,
        'first_win_seconds': None if win_row is None else win_row.get('completed_wall_seconds'),
        'evaluations': len(ledger), 'classifications': dict(sorted(by_class.items())),
        'infra_failures_before_last': infra,
    }
    if scan_checkpoints:
        row['evaluations_with_checkpoints'] = checkpoints(seed_dir, ledger)
    reasons = []
    if winner is None and stop != 'time_budget':
        reasons.append('neither won nor reached the time budget: ' + str(stop))
    if infra:
        reasons.append(f'{len(infra)} infrastructure failure(s) at or before serial {last}')
    if replay_tag:
        report = REPORTS / replay_tag / (run.name + '.json')
        if report.exists():
            r = read(report)
            row['replay'] = {'tag': replay_tag, 'passed': bool(r.get('passed')), 'planner_sha256': r.get('planner_sha256'),
                             'error': r.get('error')}
            if not r.get('passed'):
                reasons.append('golden replay failed')
        else:
            row['replay'] = {'tag': replay_tag, 'passed': None}
            reasons.append('not in the golden replay')
    else:
        reasons.append('no golden replay given (--replay-tag)')
    row['control_eligible'] = not reasons
    row['not_eligible_because'] = reasons
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('runs', nargs='*')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--replay-tag', help='tools/replay_golden.py tag whose reports decide replay eligibility')
    parser.add_argument('--checkpoints', action='store_true', help='also count evaluations with retained checkpoints (one listing per evaluation)')
    args = parser.parse_args()
    pin('e')
    runs = [Path(r) for r in args.runs] or [run for pattern in DEFAULT_GLOBS for run in sorted(ART.glob(pattern))]
    rows, skipped = [], []
    for run in runs:
        try:
            rows.append(row_of(run, args.replay_tag, args.checkpoints))
        except (OSError, ValueError, KeyError, IndexError) as exc:
            skipped.append({'run': str(run), 'error': repr(exc)})
    by_host = {}
    for r in rows:
        by_host.setdefault(r['host_key'], {'host': r['host'], 'runs': 0, 'eligible': 0})
        by_host[r['host_key']]['runs'] += 1
        by_host[r['host_key']]['eligible'] += r['control_eligible']
    library = {'schema': SCHEMA, 'created': now(), 'replay_tag': args.replay_tag,
               'harness_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               'hosts': by_host, 'runs': rows, 'skipped': skipped}
    write(args.out, library)
    print(json.dumps({'runs': len(rows), 'eligible': sum(r['control_eligible'] for r in rows),
                      'hosts': {k: (v['runs'], v['eligible']) for k, v in by_host.items()}, 'skipped': len(skipped)}))


if __name__ == '__main__':
    main()
