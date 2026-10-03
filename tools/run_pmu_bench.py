"""Detached, frozen fixed-workload P7/P14 ABBA PMU capture; never a campaign run."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from spire_exact.planning.io import write_json
from spire_exact.planning.resources import available_memory
from tools.run_seed import run_base, spawn_detached


class PdhSampler:
    """English PDH names avoid locale-dependent Get-Counter lookup."""
    class Value(ctypes.Structure):
        _fields_ = [('status', ctypes.c_ulong), ('value', ctypes.c_double)]

    def __init__(self):
        self.api = ctypes.WinDLL('pdh.dll')
        self.query = ctypes.c_void_p()
        self.api.PdhOpenQueryW.argtypes = [ctypes.c_wchar_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p)]
        self.api.PdhAddEnglishCounterW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p)]
        self.api.PdhCollectQueryData.argtypes = [ctypes.c_void_p]
        self.api.PdhGetFormattedCounterValue.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.POINTER(self.Value)]
        self.api.PdhCloseQuery.argtypes = [ctypes.c_void_p]
        if self.api.PdhOpenQueryW(None, 0, ctypes.byref(self.query)):
            raise RuntimeError('PDH query failed')
        names = [f'\\Processor Information(0,{cpu})\\% Processor Performance' for cpu in range(0, 16, 2)]
        names += ['\\Memory\\Available MBytes', '\\Memory\\Page Reads/sec']
        self.counters, self.missing = {}, {}
        for name in names:
            handle = ctypes.c_void_p()
            code = self.api.PdhAddEnglishCounterW(self.query, name, 0, ctypes.byref(handle))
            if code: self.missing[name] = code
            else: self.counters[name] = handle
        self.api.PdhCollectQueryData(self.query)

    def sample(self):
        code = self.api.PdhCollectQueryData(self.query)
        row = {'unix': time.time(), 'collect_status': code, 'values': {}, 'missing': self.missing}
        for name, handle in self.counters.items():
            value = self.Value()
            status = self.api.PdhGetFormattedCounterValue(handle, 0x200 | 0x8000, None, ctypes.byref(value))
            row['values'][name] = {'value': value.value if status == 0 and value.status in (0, 1) else None,
                                   'api_status': status, 'counter_status': value.status}
        return row

    def close(self):
        self.api.PdhCloseQuery(self.query)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--pilot-cases', type=Path, required=True)
    parser.add_argument('--iteration', default='iteration-053')
    parser.add_argument('--reserve-mib', type=int, default=1024)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--detach', action='store_true')
    args = parser.parse_args()
    folder = run_base(args.iteration)
    if args.detach:
        command = [sys.executable, Path(__file__).resolve(), *[v for v in sys.argv[1:] if v != '--detach']]
        suffix = '-resume' if args.resume else ''
        pid = spawn_detached(command, folder / ('pmu-controller' + suffix + '.log'), ROOT)
        write_json(folder / ('pmu-controller' + suffix + '.json'), {'detached_parent_pid': pid, 'command': list(map(str, command))})
        print(json.dumps({'detached_pid': pid, 'folder': str(folder)})); return
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    if not kernel.SetProcessAffinityMask(kernel.GetCurrentProcess(), (1 << 28) | (1 << 29)):
        raise ctypes.WinError(ctypes.get_last_error())
    from tools.rolling_storage import admission
    storage = admission(ROOT)
    state = {'state': 'running', 'controller_pid': os.getpid(), 'started_unix': time.time(), 'jobs': [],
             'workspace': str(args.workspace.resolve()), 'storage': storage, 'scope': 'fixed fight hardware diagnostic'}
    path = folder / 'pmu-status.json'
    if args.resume:
        prior = json.loads(path.read_text(encoding='utf-8'))
        if prior['state'] != 'needs_review': raise RuntimeError('Resume only an inspected stopped controller')
        write_json(folder / 'pmu-status-first-attempt.json', prior)
        state['jobs'] = prior['jobs']
        state['resume_note'] = 'Coordinator admission reserve only reduced; same 1024 MiB RSS guard, 768 MiB GC heap and 24 GiB enforced Job. Prior failed admission retained, no measured work was submitted.'
    write_json(path, state)
    note = 'User 2026-10-02 authorized P7/P14 SMT capture after stopping P jobs; relaxed memory to 24 GiB Job'
    env = dict(os.environ, PYTHONIOENCODING='utf-8', SPIRE_JOB_MEMORY_MIB='24576', SPIRE_RESOURCE_OVERRIDE_NOTE=note)
    from spire_exact.planning.gates import preset
    gates = preset('escalate')
    gates['plans']['FinalBoss'] = gates['final']['open']
    common = ['--runtime-profile', 'server-bounded-large-gen0', '--worker-memory-mib', '1024', '--reserve-mib', str(args.reserve_mib),
              '--normal-nodes', '10000', '--gate-preset', 'escalate', '--max-jobs', '128',
              '--advisor-json', json.dumps({'gate_plans': gates['plans']}, separators=(',', ':'))]
    runner = args.workspace.resolve() / 'tools/run_fight_bench_job.py'

    def run(name, cases, cpus, workers, extra):
        required = (workers * 1024 + args.reserve_mib) * 1024 ** 2
        if available_memory() < required:
            raise RuntimeError('Exact worker count cannot fit currently available physical memory')
        command = [sys.executable, str(runner), '--cases', str(cases.resolve()), '--out', str(folder / name),
                   '--cpus', cpus, '--workers', str(workers), *common, *extra]
        row = {'name': name, 'state': 'running', 'started_unix': time.time(), 'command': command}
        state['jobs'].append(row); write_json(path, state)
        stop = threading.Event()
        def sample():
            sampler = None
            with (folder / (name + '-system.jsonl')).open('x', encoding='utf-8') as stream:
                try:
                    sampler = PdhSampler()
                    while not stop.is_set():
                        stream.write(json.dumps(sampler.sample()) + '\n'); stream.flush(); stop.wait(1)
                except Exception as error:
                    stream.write(json.dumps({'sampler_error': repr(error), 'unix': time.time()}) + '\n')
                finally:
                    if sampler: sampler.close()
        observer = threading.Thread(target=sample, daemon=True); observer.start()
        try:
            with (folder / (name + '-launcher.log')).open('x', encoding='utf-8') as log:
                proc = subprocess.Popen(command, cwd=args.workspace, env=env, stdout=log, stderr=subprocess.STDOUT,
                                        creationflags=subprocess.CREATE_NO_WINDOW)
                row['launcher_pid'] = proc.pid; write_json(path, state)
                code = proc.wait()
        finally:
            stop.set(); observer.join()
        row.update(state='finished', exit_code=code, finished_unix=time.time()); write_json(path, state)
        if code: raise RuntimeError(f'{name} failed: see its launcher log')
        results = [json.loads(line) for line in (folder / name / 'results.jsonl').read_text().splitlines()]
        if not results or any(r.get('error') or r.get('reported_time_boundary') for r in results):
            raise RuntimeError(f'{name} contains failed / clock-limited requests')
        return results

    try:
        cases = folder / 'fixed-cases.json'
        if args.resume:
            selected = json.loads(cases.read_text())['selection']['pilot_result']
        else:
            pilot = run('pilot', args.pilot_cases, 'p', 1, ['--repeat', '1'])
            selected = sorted(pilot, key=lambda r: (-r['nodes'], r['case']))[0]
            document = json.loads(args.pilot_cases.read_text(encoding='utf-8'))
            document['cases'] = [c for c in document['cases'] if c['id'] == selected['case']]
            if len(document['cases']) != 1: raise RuntimeError('Pilot case id must identify exactly one entry')
            document['selection'] = {'rule': 'highest nodes in 8 retained ordinary fights; case id tie break',
                                     'pilot_result': selected, 'scope': 'single fixed workload; not representativeness'}
            write_json(cases, document)
        arms = [('p14-c-retry', 'p-smt', 14), ('p7-d', 'p', 7)] if args.resume else [('p7-a', 'p', 7), ('p14-b', 'p-smt', 14), ('p14-c', 'p-smt', 14), ('p7-d', 'p', 7)]
        for name, cpus, workers in arms:
            run(name, cases, cpus, workers, ['--repeat', '56', '--warmup-repeat', str(4 * workers),
                '--pmu-profile', str(args.workspace.resolve() / 'tools/pmu-generic.wprp'),
                '--pmu-out', str(folder / 'traces' / (name + '.etl'))])
        from tools.fight_bench import compare_exact
        comparisons = {name: compare_exact(folder / 'p7-a/results.jsonl', folder / name / 'results.jsonl')
                       for name in ('p14-b', 'p14-c-retry' if args.resume else 'p14-c', 'p7-d')}
        write_json(folder / 'strict-comparison.json', comparisons)
        if not all(c['equivalent'] for c in comparisons.values()):
            raise RuntimeError('Strict game/actions/nodes/request comparison failed')
        state.update(state='capture_completed', finished_unix=time.time(), strict_equivalent=True,
                     analysis_pending=True, fixed_case=selected['case'])
    except BaseException as error:
        state.update(state='needs_review', error=repr(error), finished_unix=time.time())
        write_json(path, state); raise
    write_json(path, state)
    print(json.dumps(state, ensure_ascii=False))


if __name__ == '__main__':
    main()
