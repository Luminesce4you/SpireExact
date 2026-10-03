"""Run fresh non-HOLDOUT seeds sequentially until one uses the 180-minute window.

An early verified win is retained and audited, but is not a full-window stability
pass. At most three fresh seeds are used; every run stays in the shared registry.
The normal run_seed launcher produces SpireBoard-visible directories and events.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from spire_exact.planning.io import read_json, write_json
from tools.experiment import seed_set, version_hash
from tools.seed_registry import reserve
from tools.run_seed import run_base
from tools.run_user_a10_panel import verified_row
from tools.seed_progress import summarize


def stability_checks(resources, outcomes, ledger_present):
    wall = resources.get('wall_seconds') or 0
    transferred = resources.get('job_write_transfer_bytes')
    return {
        'full_180min_protocol_window': wall >= 10770,  # default 30 s shutdown reserve
        'resource_report_enforced': resources.get('enforced') is True,
        'resource_limits_zero': outcomes.get('RESOURCE_LIMIT', 0) == 0,
        'job_peak_below_13GiB': 0 < resources.get('peak_job_commit_bytes', 0) < 13*1024**3,
        'write_rate_below_1GB_hour': transferred is not None and wall > 0 and transferred/wall*3600 <= 1_000_000_000,
        'no_storage_limit': not resources.get('storage_limit', False),
        'native_crashes_zero': outcomes.get('NATIVE_CRASH', 0) == 0,
        'no_host_or_replay_failures': all(outcomes.get(k, 0)==0 for k in
            ('MECHANISM_OR_HOST_GAP','RESTORE_OR_REPLAY_MISMATCH','INVALID_STATE','UNKNOWN')),
        'ledger_present': ledger_present,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--workspace', type=Path, required=True)
    p.add_argument('--iteration', default='iteration-039')
    p.add_argument('--name', default='infra-stability')
    p.add_argument('--max-runs', type=int, default=3)
    p.add_argument('--gate', type=Path, required=True)
    a = p.parse_args()
    if not 1 <= a.max_runs <= 3: p.error('max-runs must be 1..3')
    workspace = a.workspace.resolve()
    freeze = read_json(workspace/'freeze.json')
    if version_hash(workspace) != freeze['source_version']:
        raise ValueError('Frozen version changed')
    gate = read_json(a.gate)
    if gate.get('passed') is not True or gate.get('source_version') != freeze['source_version']:
        raise ValueError('Paired native regression gate not passed')
    base = run_base(a.iteration)
    report_path = base/(a.name+'-batch.json')
    if report_path.exists(): raise ValueError('Never overwrite a stability batch')
    report = {'protocol': 'A10-seed-v2', 'version': freeze['source_version'], 'workspace': str(workspace),
              'state': 'starting', 'runs': [], 'passed': False, 'max_runs': a.max_runs,
              'panel': 'fresh TRAIN infra diagnostics; not HOLDOUT or win-rate evaluation',
              'memory_protocol_override': '14 GiB Job + 2 GiB reserve, unchanged user-approved protocol',
              'gate': str(a.gate.resolve())}
    write_json(report_path, report)
    for ordinal in range(1, a.max_runs+1):
        name = f'{a.name}-{ordinal:02d}'
        seeds, ledger = reserve(ROOT, name, freeze['source_version'], 'TRAIN_INFRA_STABILITY', 1, None, seed_set)
        seed = seeds[0]
        row = {'seed': seed, 'name': name, 'registry': ledger, 'state': 'running'}
        report['runs'].append(row); report['state'] = 'running'; write_json(report_path, report)
        command = [sys.executable, str(ROOT/'tools/run_seed.py'), '--seed', seed, '--minutes', '180',
                   '--iteration', a.iteration, '--name', name, '--workspace', str(workspace), '--profile', 'focus',
                   '--extra', '--worker-memory-policy', 'recycle-at-boundary', '--worker-max-jobs', '64',
                   '--queue-policy', 'short-prefix-first']
        with (base/(name+'-launcher.log')).open('w', encoding='utf-8') as log:
            done = subprocess.run(command, cwd=ROOT, env=dict(os.environ, PYTHONIOENCODING='utf-8'),
                                  stdout=log, stderr=subprocess.STDOUT)
        folder = base/name
        try:
            audited = verified_row(folder, folder/'infra-audit')
            progress = summarize(folder/('seed-'+seed))
            checks = stability_checks(audited.get('resources') or {}, progress.get('classifications') or {}, progress.get('evaluations', 0)>0)
            checks['launcher_succeeded'] = done.returncode == 0
            checks['expected_exit'] = audited.get('exit_code') in (0, 124)
            checks['no_false_win_claim'] = not read_json(folder/'baseline-report.json').get('verified_win') or audited['verified_win']
            row.update(state='finished', audit=audited, progress=progress, checks=checks, passed=all(checks.values()))
            write_json(folder/'infra-stability-report.json', row)
            if row['passed']:
                report.update(state='completed', passed=True)
                write_json(report_path, report); return
            if not audited.get('verified_win') or any(not v for k,v in checks.items() if k!='full_180min_protocol_window'):
                report.update(state='needs_review', passed=False)
                write_json(report_path, report); return
            # Early native victory: retain all evidence, then test the next new seed.
            row['stability_duration_insufficient'] = True
        except Exception as error:
            row.update(state='audit_failed', error=str(error), passed=False)
            report.update(state='needs_review', passed=False)
            write_json(report_path, report); raise
        write_json(report_path, report)
    report.update(state='all_runs_ended_early', passed=False)
    write_json(report_path, report)


if __name__ == '__main__':
    main()
