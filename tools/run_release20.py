"""Conditional release20 controller; no seeds are drawn before the named win.

Use --detach to wait outside the launching app's Job. Once the source run's
existing independent certificate is checked, reserve 20 unseen numeric seeds
and run the existing serial queue with --wait-idle, never --stop-unsolved.
Create release20/queue.stop to cancel waiting or stop after the current job.
This reads existing proof artifacts; it does not execute a verification itself.
"""
from copy import deepcopy
from pathlib import Path
import argparse
import datetime
import hashlib
import json
import os
import random
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from spire_exact.canonical import canonical, digest
from spire_exact.planning.io import read_json, write_json
from tools.run_seed import spawn_detached
from tools.seed_registry import locked

SOURCE_NAME = 'final30b-524130501'
SOURCE_SEED = '524130501'
VERSION = '48bed8e089836f68320df4b8070cf779990c6adfd6cf1966c81c0b7f80925d7a'
GAME_SHA256 = '0861bfa1df347538d932f22d580e75420f08082792eb914e53b4882764acdbe9'
ITERATION = 'iteration-075-release20'
COUNT = 20
MAX_SEED = 2147483647
AUTHORIZATION = ('User 2026-10-04: after final30b-524130501 independently verified win, '
                 'randomly draw 20 new seeds as release20 and finish all serial '
                 '30-minute runs; continue after failure or timeout, no stop-unsolved.')


def timestamp():
    return datetime.datetime.now().astimezone().isoformat()


def event(directory, kind, **fields):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'controller.log.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'event': kind, 'timestamp': timestamp(), **fields}, ensure_ascii=False) + '\n')


def option(argv, key, default=None):
    prefix = argv[:argv.index('--extra')] if '--extra' in argv else argv
    if prefix.count(key) > 1:
        raise ValueError('duplicate source option: ' + key)
    if key not in prefix:
        return default
    index = prefix.index(key)
    if index + 1 == len(prefix) or prefix[index + 1].startswith('--'):
        raise ValueError('missing source option value: ' + key)
    return prefix[index + 1]


def last_setting(argv, key):
    return next((argv[i + 1] for i in range(len(argv) - 2, -1, -1) if argv[i] == key), None)


def source_job(queue, root):
    jobs = read_json(queue)
    if (not isinstance(jobs, list) or len(jobs) != 1 or not isinstance(jobs[0], list)
            or any(not isinstance(arg, str) for arg in jobs[0])):
        raise ValueError('source queue must contain the single authorized job')
    job = jobs[0]
    expected = {'--name': SOURCE_NAME, '--seed': SOURCE_SEED, '--minutes': '30',
                '--profile': 'focus', '--feature-profile': 'i075-final', '--workers': '7',
                '--logical-cpus': '8', '--panel': 'USER_INTERACTIVE'}
    if any(option(job, key) != value for key, value in expected.items()):
        raise ValueError('source job differs from the authorized final-gzip configuration')
    if '--detach' in job or '--stop-unsolved' in job or any(arg.startswith('--no-') for arg in job):
        raise ValueError('source job has unexpected feature or execution overrides')
    if option(job, '--job-memory-mib', '14336') != '14336':
        raise ValueError('source Job memory differs')
    workspace = Path(option(job, '--workspace')).resolve()
    if workspace != (root / 'experiments/frozen-i075-final-gzip').resolve():
        raise ValueError('source workspace differs')
    if read_json(workspace / 'freeze.json').get('source_version') != VERSION:
        raise ValueError('source frozen version differs')
    extra = job[job.index('--extra') + 1:] if '--extra' in job else []
    for key, value in {'--runtime-profile': 'server-bounded-large-gen0',
                       '--gate-preset': 'escalate-evaluate'}.items():
        if last_setting(extra, key) != value:
            raise ValueError('source setting differs: ' + key)
    return deepcopy(job)


def finished_row(log, job):
    if not log.exists():
        return None
    rows = []
    # The writer appends one complete line per finished job. A partial final
    # write is not a completion signal; unrelated runs never trigger this gate.
    with log.open(encoding='utf-8-sig') as stream:
        for line in stream:
            if not line.endswith('\n'):
                continue
            row = json.loads(line)
            if isinstance(row, dict) and row.get('job') == job and 'exit_code' in row:
                rows.append(row)
    if len(rows) > 1:
        raise ValueError('multiple completions for the source job')
    return rows[0] if rows else None


def summary(row):
    lines = (row.get('stdout') or '').strip().splitlines()
    value = json.loads(lines[-1]) if lines else {}
    if not isinstance(value, dict):
        raise ValueError('source completion summary is not an object')
    return value


def file_sha256(path):
    sha = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            sha.update(block)
    return sha.hexdigest()


def verified_condition(root, job, row):
    """Bind existing certificate/route/report to this exact frozen run."""
    note = summary(row)
    if (row.get('exit_code') != 0 or note.get('verified_win') is not True
            or note.get('exit_code') != 0 or note.get('seed') != SOURCE_SEED):
        raise ValueError('named source run did not independently verify victory')
    artifact = Path(read_json(root / 'storage-policy.json')['artifact_roots'][0]).resolve()
    folder = artifact / option(job, '--iteration') / SOURCE_NAME
    target = folder / ('seed-' + SOURCE_SEED)
    manifest = read_json(folder / 'validation-manifest.json')
    report = read_json(folder / 'baseline-report.json')
    result = read_json(target / 'result.json')
    certificate = read_json(target / 'certificate.json')
    route = read_json(target / 'winning-route.json')
    workspace = Path(option(job, '--workspace')).resolve()
    if (manifest.get('run_id') != SOURCE_NAME or manifest.get('seed') != SOURCE_SEED
            or manifest.get('version') != VERSION or manifest.get('workspace_frozen') is not True
            or Path(manifest.get('workspace', '')).resolve() != workspace
            or manifest.get('wall_cap_seconds') != 1800 or manifest.get('profile') != 'focus'
            or manifest.get('panel') != 'USER_INTERACTIVE'
            or manifest.get('workload_bytes') != 14336 * 1024 ** 2
            or len(set(manifest.get('cpu_set', []))) != 8 or manifest.get('efficiency_class') != 1):
        raise ValueError('source manifest identity/resources mismatch')
    settings = manifest.get('settings', [])
    for key, value in {'--seed': SOURCE_SEED, '--character': 'IRONCLAD', '--ascension': '10',
                       '--unlocks': 'all', '--workers': '7', '--feature-profile': 'i075-final',
                       '--runtime-profile': 'server-bounded-large-gen0',
                       '--gate-preset': 'escalate-evaluate'}.items():
        if last_setting(settings, key) != value:
            raise ValueError('actual source setting mismatch: ' + key)
    if (report.get('seed') != SOURCE_SEED or report.get('verified_win') is not True
            or report.get('exit_code') != 0 or result.get('status') != 'VERIFIED_WIN_IN_NATIVE_HOST'
            or result.get('optimal_in_backend') is not True
            or any(report.get('resources', {}).get(key) for key in ('hard_timeout', 'storage_limit'))):
        raise ValueError('source final report/result mismatch')
    objective = {'id': 'whole_run_victory/v1', 'information': 'full', 'minimum': 0,
                 'maximum': 1, 'secondary_objective': None}
    identity = certificate.get('identity') or {}
    context, native = identity.get('context') or {}, identity.get('native') or {}
    if (certificate.get('schema') != 'spire-mode1-certificate/v1'
            or certificate.get('objective') != objective or context.get('objective') != objective
            or context.get('seed') != SOURCE_SEED or context.get('character') != 'IRONCLAD'
            or context.get('ascension') != 10 or context.get('unlocks') != 'all'
            or certificate.get('lower_bound') != 1 or certificate.get('upper_bound') != 1
            or certificate.get('optimal_in_backend') is not True
            or certificate.get('proves_no_better_boolean_value') is not True
            or native.get('game_sha256') != GAME_SHA256
            or native.get('host_sha256') != manifest.get('host_sha256')
            or file_sha256(workspace / 'native/SpireNativeHost/bin/Release/net9.0/SpireNativeHost.dll') != native.get('host_sha256')):
        raise ValueError('independent certificate identity mismatch')
    if (canonical(route.get('context')) != canonical(context)
            or canonical(result.get('context')) != canonical(context)
            or not isinstance(route.get('trace'), list) or not route['trace']
            or len(route['trace']) != certificate.get('trace_length')
            or digest(route['trace']) != certificate.get('trace_sha256')):
        raise ValueError('certified complete route mismatch')
    return {'run': str(folder), 'source_version': VERSION, 'host_sha256': native['host_sha256'],
            'certificate': str(target / 'certificate.json'),
            'certificate_sha256': file_sha256(target / 'certificate.json'),
            'trace_sha256': certificate['trace_sha256'], 'summary': note}


def seed_values(value):
    """Read identities only; no outcome/trajectory data feeds seed selection."""
    found = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == 'seed':
                found.update(numeric_seeds([item]))
            elif key in ('seeds', 'reserved') and isinstance(item, list):
                found.update(numeric_seeds(item))
            else:
                found.update(seed_values(item))
    elif isinstance(value, list):
        for item in value:
            found.update(seed_values(item))
    return found


def numeric_seeds(values):
    return {str(int(value)) for value in values
            if type(value) in (str, int) and str(value).isdigit() and 0 <= int(value) <= MAX_SEED}


def excluded_seeds(root):
    roots = [root, root.parent / 'P5plus']
    paths = set()
    for owner in roots:
        ledger = owner / 'experiments/seed-ledger.json'
        if ledger.exists():
            paths.add(ledger)
        paths.update((owner / 'experiments').glob('frozen-*/experiments/seed-ledger.json'))
        paths.update((owner / 'experiments/panels').rglob('*.json'))
    values = {SOURCE_SEED}
    for path in sorted(paths):
        values.update(seed_values(read_json(path)))
    return values, [str(path) for path in sorted(paths)]


def save_new(path, value):
    # Exclusive publication: partial failures are inspectable and cannot redraw
    # or overwrite the seeds on the next invocation.
    with path.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def generate_release(root, directory, job, proof, rng=None):
    ledger_path = root / 'experiments/seed-ledger.json'
    with locked(ledger_path.with_suffix('.lock')):
        files = ('seeds.json', 'queue.json', 'manifest.json', 'authorization.json')
        if any((directory / name).exists() for name in files):
            raise ValueError('release20 already generated or partially published; never redraw/overwrite')
        ledger = read_json(ledger_path)
        excluded, sources = excluded_seeds(root)
        rng = rng if rng is not None else random.SystemRandom()
        seeds = []
        while len(seeds) < COUNT:
            seed = str(rng.randrange(MAX_SEED + 1))
            if seed not in excluded:
                seeds.append(seed)
                excluded.add(seed)
        queue = []
        for index, seed in enumerate(seeds, 1):
            run_id = 'release20-%02d-%s' % (index, seed)
            if any(row.get('run_id') == run_id for row in ledger['runs']):
                raise ValueError('release20 run id already reserved')
            argv = deepcopy(job)
            for key, value in {'--seed': seed, '--name': run_id, '--iteration': ITERATION,
                               '--minutes': '30', '--panel': 'USER_INTERACTIVE'}.items():
                argv[argv.index(key) + 1] = value
            queue.append(argv)
            ledger['runs'].append({'run_id': run_id, 'version': VERSION, 'kind': 'user_interactive',
                                   'seeds': [seed], 'not_a_holdout_result': True,
                                   'release20': True, 'reserved': True})
        # Reserve before exposing runnable jobs. Any publication failure keeps
        # these seeds reserved and the controller reports error rather than retry.
        write_json(ledger_path, ledger)
        common = {'schema': 'spire-release20/v1', 'role': 'USER_INTERACTIVE',
                  'not_a_holdout_result': True, 'count': COUNT, 'opened': timestamp(),
                  'source_version': VERSION, 'condition': proof, 'authorization': AUTHORIZATION}
        save_new(directory / 'seeds.json', {**common, 'seeds': seeds,
                 'selection': 'SystemRandom uniform numeric seeds, without replacement',
                 'range': [0, MAX_SEED], 'exclusion_sources': sources})
        save_new(directory / 'queue.json', queue)
        save_new(directory / 'authorization.json', common)
        save_new(directory / 'manifest.json', {**common, 'seeds': seeds, 'iteration': ITERATION,
                 'workspace': option(job, '--workspace'), 'source_job': job,
                 'minutes': 30, 'workers': 7, 'logical_cpus': 8, 'cpu_class': 'P',
                 'job_memory_mib': 14336, 'serial': True, 'continue_after_unsolved': True,
                 'queue': str(directory / 'queue.json'), 'stop_file': str(directory / 'queue.stop')})
    return queue


def completed_jobs(log, jobs):
    rows = []
    if log.exists():
        for line in log.read_text(encoding='utf-8-sig').splitlines():
            row = json.loads(line)
            index = row.get('index')
            if (type(index) is int and 0 <= index < len(jobs)
                    and row.get('job') == jobs[index] and 'exit_code' in row):
                rows.append(row)
    if len({row['index'] for row in rows}) != len(rows):
        raise ValueError('duplicate release20 completion')
    return rows


def control(root, source_queue, source_log, directory, poll_seconds=20, *, sleep=time.sleep, run=subprocess.run):
    directory.mkdir(parents=True, exist_ok=True)
    stop = directory / 'queue.stop'
    try:
        # Never resume/relaunch a controller implicitly after an interrupted job.
        save_new(directory / 'controller-claim.json', {'pid': os.getpid(), 'created': timestamp()})
        job = source_job(source_queue, root)
        event(directory, 'waiting', source_run=SOURCE_NAME, source_log=str(source_log))
        while not stop.exists():
            row = finished_row(source_log, job)
            if row is not None:
                break
            sleep(poll_seconds)
        else:
            event(directory, 'cancelled', reason='user stop file while waiting')
            return 0
        try:
            proof = verified_condition(root, job, row)
        except (OSError, ValueError, TypeError, KeyError) as error:
            event(directory, 'condition-failed', reason=str(error))
            return 0
        if stop.exists():
            event(directory, 'cancelled', reason='user stop file before seed generation')
            return 0
        jobs = generate_release(root, directory, job, proof)
        event(directory, 'generated', count=len(jobs), seeds=str(directory / 'seeds.json'))
        command = [sys.executable, str(root / 'tools/run_queue.py'), '--wait-idle', str(directory / 'queue.json')]
        event(directory, 'started', command=command, continue_after_unsolved=True)
        done = run(command, cwd=root, capture_output=True, text=True,
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        rows = completed_jobs(directory / 'queue.log.jsonl', jobs)
        if done.returncode != 0 or (len(rows) != COUNT and not stop.exists()):
            raise RuntimeError('queue ended unexpectedly: exit=%s, finished=%s/20; %s' %
                               (done.returncode, len(rows), (done.stderr or done.stdout or '')[-1500:]))
        event(directory, 'cancelled' if len(rows) != COUNT else 'completed',
              finished=len(rows), exit_code=done.returncode,
              scope='queue completion only; each run retains its own UNKNOWN/win report')
        return 0
    except Exception as error:
        event(directory, 'error', error_type=type(error).__name__, reason=str(error))
        return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--detach', action='store_true')
    parser.add_argument('--source-queue', type=Path, default=ROOT / 'experiments/iteration-075/final-gzip-queue.json')
    parser.add_argument('--source-log', type=Path, default=None)
    parser.add_argument('--release-dir', type=Path, default=ROOT / 'experiments/iteration-075/release20')
    parser.add_argument('--poll-seconds', type=int, default=20)
    args = parser.parse_args(argv)
    if not 1 <= args.poll_seconds <= 60:
        parser.error('poll-seconds must be 1..60')
    queue = args.source_queue.resolve()
    log = (args.source_log or queue.with_suffix('.log.jsonl')).resolve()
    directory = args.release_dir.resolve()
    if args.detach:
        directory.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, str(Path(__file__).resolve()), '--source-queue', str(queue),
                   '--source-log', str(log), '--release-dir', str(directory), '--poll-seconds', str(args.poll_seconds)]
        pid = spawn_detached(command, directory / 'controller.log', ROOT)
        print(json.dumps({'detached_pid': pid, 'release_dir': str(directory), 'condition_run': SOURCE_NAME}))
        return 0
    for name in ('SIGINT', 'SIGBREAK'):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), signal.SIG_IGN)
    return control(ROOT, queue, log, directory, args.poll_seconds)


if __name__ == '__main__':
    raise SystemExit(main())
