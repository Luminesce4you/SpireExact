"""Harmless Windows runtime check for whole-job pause/resume/paused exit.

Creates only short counter processes. Does not initialize STS2 or run a seed.
"""
from pathlib import Path
import argparse, json, os, subprocess, sys, time
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dashboard import job_control
from spire_exact.planning.io import write_json


def counter(folder, role):
    if role in ('coordinator', 'nested'):
        job_control.initialize(folder / role)
        job_control.mark_running(folder / role)
    if role != 'leaf':
        child = 'nested' if role == 'coordinator' else 'leaf'
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--worker', child,
                          '--out', str(folder)], cwd=ROOT,
                         creationflags=subprocess.CREATE_NO_WINDOW)
    n = 0
    while True:
        write_json(folder / f'{role}-counter.json', {'pid': os.getpid(), 'counter': n})
        n += 1
        time.sleep(.02)


def read_counter(path):
    from dashboard.server import read
    return read(path, {})


def await_all(folder, condition, seconds=5):
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        rows = {name: read_counter(folder / f'{name}-counter.json') for name in ('coordinator', 'nested', 'leaf')}
        if condition(rows):
            return rows
        time.sleep(.02)
    raise AssertionError('Counter condition timed out')


def main():
    p = argparse.ArgumentParser(); p.add_argument('--out', type=Path, required=True)
    p.add_argument('--worker', choices=('coordinator', 'nested', 'leaf'))
    a = p.parse_args(); folder = a.out.resolve(); folder.mkdir(parents=True, exist_ok=True)
    if a.worker:
        if a.worker != 'leaf': (folder / a.worker).mkdir(exist_ok=True)
        counter(folder, a.worker)
        return
    if os.name != 'nt': raise SystemExit('Windows runtime check required')
    with (folder / 'process.log').open('wb') as stream:
        proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--worker', 'coordinator', '--out', str(folder)],
                                cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    owner = folder / 'coordinator'
    try:
        initial = await_all(folder, lambda rows: all(r.get('counter', 0) >= 3 for r in rows.values()))
        wrong = json.loads((owner / 'control.json').read_text())
        valid = dict(wrong); valid['coordinator_identity'] = dict(wrong['coordinator_identity'])
        wrong['coordinator_identity']['created_ticks'] += 1
        write_json(owner / 'control.json', wrong)
        try:
            job_control.pause(owner)
        except RuntimeError:
            wrong_identity_rejected = True
        else:
            raise AssertionError('Changed process identity must fail closed')
        write_json(owner / 'control.json', valid)
        paused = job_control.pause(owner)
        before = await_all(folder, lambda rows: all(rows.values()))
        time.sleep(.5)
        after = await_all(folder, lambda rows: all(rows.values()))
        assert before == after, (before, after)
        pause_elapsed = job_control.status(owner)['active_elapsed_seconds']
        assert abs(pause_elapsed - paused['active_elapsed_seconds']) < .05
        resumed = job_control.resume(owner)
        progressed = await_all(folder, lambda rows: all(rows[k]['counter'] > after[k]['counter'] for k in rows))
        job_control.pause(owner)
        stopped = job_control.cancel(owner)
        proc.wait(timeout=5)
        from dashboard.jobs import process_identity
        deadline = time.perf_counter() + 5
        while any(process_identity(r['pid']) for r in progressed.values()) and time.perf_counter() < deadline:
            time.sleep(.02)
        assert not any(process_identity(r['pid']) for r in progressed.values())
        # Bundled Python can add short launcher processes; all owned members,
        # including those launchers, must be held, at least the three counters.
        assert paused['phase'] == 'paused' and paused['frozen_process_count'] >= 3, paused
        assert resumed['phase'] == 'running' and stopped['phase'] == 'stopped'
        report = {'passed': True, 'native_game_runs': 0, 'scope': 'three harmless counter processes in nested Windows Jobs',
                  'freeze_counters_identical': before == after, 'resume_all_counters_progressed': True,
                  'cancel_while_paused_terminated_all': True, 'creation_identity_mismatch_rejected': wrong_identity_rejected,
                  'pause_budget_elapsed_frozen': True, 'frozen_process_count': paused['frozen_process_count'],
                  'pause_seconds': .5, 'initial': initial, 'before': before, 'after': after,
                  'resumed': progressed, 'paused_status': paused, 'final_status': stopped}
        write_json(folder / 'report.json', report)
        print(json.dumps({k: v for k, v in report.items() if k not in ('initial', 'before', 'after', 'resumed')}, indent=2))
    finally:
        if proc.poll() is None:
            try: job_control.cancel(owner)
            except (OSError, RuntimeError): proc.terminate()
            proc.wait(timeout=5)


if __name__ == '__main__': main()
