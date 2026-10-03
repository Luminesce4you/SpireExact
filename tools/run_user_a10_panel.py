"""One frozen version, six fresh A10 games, sequential completion-driven jobs.

Preparation does not launch games. --execute is used only after optimization
convergence and the recorded promotion gates, never by the current baseline hook.
"""
from pathlib import Path
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from spire_exact.planning.io import read_json, write_json
from tools.experiment import version_hash
from tools.seed_registry import locked

SEEDS = ['10101010', '101010', '1010', '10', '1', '0']


def settings(spec, seed):
    return ['--seed', seed, '--character', 'IRONCLAD', '--ascension', '10', '--unlocks', 'all',
            '--workers', '7', '--dop', '1', '--worker-memory-mib', '1536', '--reserve-mib', '1024',
            '--evaluations', '1000000', '--seconds', '10770', '--task-seconds', '1200',
            '--max-decisions', '12000', '--lookahead-actions', '12000', '--lookahead-floors', '99',
            '--alternatives', '8', '--survivors', '2', '--budget-ms', '600000', '--boss-budget-ms', '600000',
            '--nodes', '60000', '--profile', 'Low', '--dispatch', 'ordered', '--dispatch-window', '56',
            '--solver-seed', '271828', '--low-io', '--event-driven-settle', '--checkpoint-mib', '1024',
            '--cache-mib', '128', '--archive-entries', '256', '--runtime-profile', spec['runtime_profile'],
            '--queue-policy', spec['queue_policy']]


def validate_spec(spec, root=ROOT):
    workspace = Path(spec['workspace']).resolve()
    experiments = (root / 'experiments').resolve()
    if workspace.parent != experiments or not workspace.name.startswith('frozen-'):
        raise ValueError('Use a frozen workspace directly under experiments')
    if spec.get('seeds') != SEEDS:
        raise ValueError('Panel must contain all six requested seeds in the fixed order')
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', spec.get('run_id', '')):
        raise ValueError('Invalid panel run id')
    if spec.get('runtime_profile') not in ('legacy', 'workstation-2', 'server-one-heap'):
        raise ValueError('Unknown runtime profile')
    if spec.get('queue_policy') not in ('fifo', 'short-prefix-first'):
        raise ValueError('Unknown queue policy')
    frozen = read_json(workspace / 'freeze.json')
    if spec.get('version') != frozen.get('source_version') or version_hash(workspace) != spec.get('version'):
        raise ValueError('Frozen source version does not match the panel spec')
    if spec.get('optimization_convergence_record') is None:
        raise ValueError('Optimization convergence evidence must be recorded before execution')
    convergence = read_json(Path(spec['optimization_convergence_record']))
    if convergence.get('ready_for_user_panel') is not True or convergence.get('version') != spec['version']:
        raise ValueError('Convergence record does not authorize this frozen version')
    gates = spec.get('gates') or []
    if not gates:
        raise ValueError('At least one native promotion gate is required')
    for gate in gates:
        if read_json(Path(gate['path'])).get(gate['field']) is not True:
            raise ValueError('Native promotion gate failed: ' + gate['path'])
    return workspace


def verified_row(folder, audit_dir):
    """A report flag or certificate filename alone is never a win."""
    from tools.audit_a10_baseline import audit
    report = read_json(folder / 'baseline-report.json')
    try:
        audit(folder, audit_dir)
        evidence = read_json(audit_dir / 'audit.json')
        win = report.get('verified_win') is True and evidence.get('evidence_valid') is True and bool(evidence.get('witness'))
        resources = report.get('resources') or {}
        outcomes = evidence.get('classifications') or {}
        reason = ('VERIFIED_NATIVE_WIN' if win else 'STORAGE_LIMIT' if resources.get('storage_limit') else
                  'TIMEOUT' if resources.get('hard_timeout') or report['exit_code'] == 124 else
                  'EVIDENCE_AUDIT_ERROR' if report.get('verified_win') else
                  'NATIVE_CRASH' if outcomes.get('NATIVE_CRASH') else
                  'UNSUPPORTED_MECHANIC' if outcomes.get('MECHANISM_OR_HOST_GAP') else
                  'EXECUTION_ERROR' if report['exit_code'] else 'SEARCH_EXHAUSTED')
        return {'seed': report['seed'], 'verified_win': win, 'censored': not win,
                'exit_code': report['exit_code'], 'audit': str(audit_dir / 'audit.json'),
                'wall_seconds': report['wall_seconds'], 'resources': report['resources'],
                'classification': reason,
                'outcomes': evidence.get('classifications'), 'witness': evidence.get('witness'),
                'performance_diagnosis': evidence.get('performance_diagnosis')}
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        return {'seed': report['seed'], 'verified_win': False, 'censored': True,
                'classification': 'EVIDENCE_AUDIT_ERROR', 'error': str(error), 'exit_code': report['exit_code']}


def run(spec, out):
    workspace = validate_spec(spec)
    from dashboard.jobs import process_identity, manual_base
    from dashboard.manual_runner import wait_original
    from dashboard.server import Repository
    from dashboard.jobs import blockers
    from tools.rolling_storage import admission, sweep, active_seed, RollingConsole, logical_bytes
    from tools.experiment_storage import prepare_storage
    from tools.cpu_topology import inventory, homogeneous_cpus
    import ctypes as c
    # Capture existing reservations before adding this batch's own reservation.
    previous = blockers(Repository())
    out.mkdir(parents=True, exist_ok=False)
    prepare_storage(out); admission(ROOT)
    identity = process_identity(os.getpid())
    write_json(ROOT / 'dashboard/runtime/reservations' / (spec['run_id'] + '.json'),
               {**identity, 'purpose': 'sequential user six-seed A10 panel'})
    write_json(out / 'panel-manifest.json', {**spec, 'protocol': 'A10-seed-v2', 'panel': 'USER_DIAGNOSTIC',
               'workload_bytes': 14*1024**3, 'os_reserve_bytes': 2*1024**3,
               'memory_protocol_override': 'User authorized 16 GiB total',
               'report_point_seconds': 9000, 'per_seed_wall_cap_seconds': 10800,
               'settings_by_seed': {seed: settings(spec, seed) for seed in SEEDS},
               'wait_for': previous, 'old_prefixes_loaded': False, 'not_a_generalization_estimate': True})
    for item in previous:
        wait_original(item)
    cpus, cls = homogeneous_cpus(inventory(), 8)
    k = c.WinDLL('kernel32', use_last_error=True); k.GetCurrentProcess.restype = c.c_void_p
    k.SetProcessAffinityMask.argtypes = [c.c_void_p, c.c_size_t]
    if not k.SetProcessAffinityMask(k.GetCurrentProcess(), sum(1 << cpu for cpu in cpus)):
        raise c.WinError(c.get_last_error())
    ledger = ROOT / 'experiments/seed-ledger.json'
    with locked(ledger.with_suffix('.lock')):
        entries = read_json(ledger)
        if any(r['run_id'] == spec['run_id'] for r in entries['runs']):
            raise ValueError('Panel id already registered; never silently rerun or overwrite')
        entries['runs'].append({'run_id': spec['run_id'], 'seeds': SEEDS, 'version': spec['version'],
                               'kind': 'user_diagnostic', 'not_a_holdout_result': True})
        write_json(ledger, entries)
    sdk = ROOT.parent / '.tools/dotnet'
    env = dict(os.environ, DOTNET_ROOT=str(sdk), PYTHONIOENCODING='utf-8', SPIRE_PROTOCOL='A10-seed-v2', SPIRE_WALL_LIMIT_SECONDS='10800')
    env['PATH'] = str(sdk) + os.pathsep + env['PATH']
    rows = []
    for index, seed in enumerate(SEEDS):
        validate_spec(spec)  # No version changes between seeds, even after hours.
        sweep(ROOT, apply=True)  # Existing policy preserves wins and active/pinned evidence.
        admission(ROOT)
        # Keep the per-seed layout visible to the existing live dashboard.
        folder = manual_base() / (spec['run_id'] + '-' + seed); folder.mkdir()
        target = folder / ('seed-' + seed)
        write_json(folder / 'validation-manifest.json', {
            'run_id': spec['run_id'] + '-' + seed, 'seed': seed, 'panel': 'USER_DIAGNOSTIC', 'manual': True,
            'protocol': 'A10-seed-v2', 'version': spec['version'], 'workspace': str(workspace),
            'timestamp': datetime.datetime.now().astimezone().isoformat(), 'solver_seed': 271828,
            'cpu_set': cpus, 'efficiency_class': cls, 'settings': settings(spec, seed),
            'workload_bytes': 14*1024**3, 'os_reserve_bytes': 2*1024**3,
            'wall_cap_seconds': 10800, 'report_point_seconds': 9000,
            'old_prefixes_loaded': False, 'not_a_holdout_result': True})
        write_json(folder / 'launch-state.json', {'phase': 'running'})
        command = [sys.executable, str(workspace / 'tools/limited_cli.py'), 'solve-p5', '--out', str(target),
                   '--game-dir', str(workspace / 'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'), *settings(spec, seed)]
        print(json.dumps({'event': 'user_panel_seed_started', 'seed': seed, 'index': index}), flush=True)
        log = RollingConsole(folder / 'console.log')
        try:
            with active_seed(target), subprocess.Popen(command, cwd=workspace, env=env, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW) as child:
                write_json(folder / 'process.json', {'coordinator_pid': os.getpid(), 'coordinator_identity': identity, 'owned_job_pid': child.pid})
                for line in iter(child.stdout.readline, b''):
                    log.write(line)
                code = child.wait()
        finally:
            log.close()
        result = read_json(target / 'result.json') if (target / 'result.json').exists() else {}
        path = folder / (target.name + '-resources.json')
        resources = read_json(path) if path.exists() else {}
        claimed_win = code == 0 and result.get('status') == 'VERIFIED_WIN_IN_NATIVE_HOST'
        write_json(folder / 'baseline-report.json', {'seed': seed, 'verified_win': claimed_win, 'exit_code': code,
            'resources': resources, 'wall_seconds': resources.get('wall_seconds'), 'file_bytes': logical_bytes(folder),
            'not_a_seed_infeasibility_proof': True, 'normal_godot_verified': False})
        row = verified_row(folder, out / 'audits' / seed); row['run'] = str(folder.resolve()); rows.append(row)
        write_json(folder / 'launch-state.json', {'phase': 'completed' if code in (0, 124) else 'error'})
        write_json(out / 'results.json', {'rows': rows, 'planned_seeds': SEEDS, 'complete': len(rows) == len(SEEDS),
                   'verified_wins': sum(r['verified_win'] for r in rows), 'not_a_generalization_estimate': True})
        print(json.dumps({'event': 'user_panel_seed_completed', 'seed': seed, 'verified_win': row['verified_win'], 'exit_code': code}), flush=True)
    return rows


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--spec', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True); p.add_argument('--execute', action='store_true')
    a = p.parse_args(); spec = read_json(a.spec)
    if a.execute:
        run(spec, a.out.resolve())
    else:
        validate_spec(spec)
        print(json.dumps({'will_execute': False, 'seeds': SEEDS, 'settings_by_seed': {s: settings(spec, s) for s in SEEDS}}))
