"""B1 full-byte functional gate and four alternating, warmed P7 timing pairs.

The controller runs on E cores; each child owns its Windows Job. It waits for
other STS2 campaign queues before timing and never stops their processes.
Use run_seed.spawn_detached to preserve its completion handle across app exits.
"""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.fight_bench import compare_exact
from tools.audit_infra_bench import audit_pair
from spire_exact.planning.io import write_json
from spire_exact.planning.resources import available_memory


def external_campaigns():
    script = ("Get-CimInstance Win32_Process | Where-Object { "
              "$_.Name -in @('python.exe','dotnet.exe') -and $_.CommandLine -like '*STS2*' -and "
              "($_.CommandLine -like '*run_queue.py*' -or "
              "$_.CommandLine -like '*run_seed.py*' -or "
              "$_.CommandLine -match 'limited_cli.py.*solve-p5') } | "
              "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress")
    done = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive',
                           '-Command', script], capture_output=True, text=True,
                          creationflags=subprocess.CREATE_NO_WINDOW)
    if done.returncode:
        raise RuntimeError('Cannot establish external campaign inventory')
    value = json.loads(done.stdout or '[]')
    return value if isinstance(value, list) else [value]


def process_sample():
    """CPU deltas and affinity detect other compute jobs during P7 timing."""
    script = ("$parents=@{}; Get-CimInstance Win32_Process | ForEach-Object {$parents[[int]$_.ProcessId]=[int]$_.ParentProcessId}; "
              "$values=@(Get-Process | ForEach-Object {try { "
              "[pscustomobject]@{pid=$_.Id;parent=$parents[[int]$_.Id];name=$_.Name;cpu=$_.CPU;affinity=$_.ProcessorAffinity.ToInt64()} "
              "} catch {} }); ConvertTo-Json -Compress -InputObject $values")
    done = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                          capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    if done.returncode:
        raise RuntimeError('Cannot sample timing interference')
    return {r['pid']: r for r in json.loads(done.stdout)}


BACKGROUND_ALLOWLIST = {'AppHelperCap', 'OmenCommandCenterBackground', 'msedgewebview2', 'svchost'}
# Observed resident HP/Windows desktop services, logged as noise rather than
# called an idle system. Other compute processes remain disallowed.
COMPUTE_PROCESS_NAMES = {'python', 'pythonw', 'dotnet', 'blender', 'FreeCAD', 'MATLAB',
                         'java', 'javaw', 'Rscript', 'ffmpeg', '7z', 'godot', 'sts2'}


def external_load(previous, current, child_pid=None):
    owned = {os.getpid()}
    if child_pid is not None:
        owned.add(child_pid)
    for _ in range(len(current)):
        added = {pid for pid, r in current.items() if r['parent'] in owned}-owned
        if not added:
            break
        owned.update(added)
    return [dict(r, cpu_delta=r['cpu']-previous[pid]['cpu']) for pid, r in current.items()
            if pid not in owned and pid in previous and r.get('cpu') is not None
            and previous[pid].get('cpu') is not None and r['affinity'] & 0xFFFF
            and r['cpu']-previous[pid]['cpu'] > 0.05]


def busy_external(previous, current, child_pid=None):
    return [r for r in external_load(previous, current, child_pid)
            if r['name'] in COMPUTE_PROCESS_NAMES and r['cpu_delta'] > 2]


def rows(folder):
    return [json.loads(line) for line in (folder/'results.jsonl').read_text(encoding='utf-8').splitlines()]


def totals(folder):
    records = rows(folder)
    return {'cases': len(records), 'nodes': sum(r['nodes'] for r in records),
            'search_wall': sum(r['search_wall'] for r in records),
            'allocated_gb': sum(r['allocated_gb'] for r in records),
            'gc_pause': sum(r['gc_pause'] for r in records)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workspace', type=Path, required=True)
    p.add_argument('--baseline', type=Path, required=True)
    p.add_argument('--candidate', type=Path, required=True)
    p.add_argument('--cases', type=Path, nargs='+', required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--mode', choices=['functional', 'performance'], required=True)
    p.add_argument('--after-status', type=Path, help='Require this completed functional gate before timing')
    p.add_argument('--worker-memory-mib', type=int, default=1792)
    p.add_argument('--reserve-mib', type=int)
    p.add_argument('--reuse-functional-root', type=Path,
                   help='Reaudit completed functional arms without overwriting/repeating them')
    p.add_argument('--reuse-performance-root', type=Path, nargs='+',
                   help='Reuse an identical successfully measured arm after a controller-only fix')
    a = p.parse_args()
    ctypes.windll.kernel32.SetProcessAffinityMask(ctypes.windll.kernel32.GetCurrentProcess(), 0x30000000)
    a.out.mkdir(parents=True, exist_ok=False)
    attempts = []
    pairs = []
    status = {'mode': a.mode, 'state': 'starting', 'attempts': attempts, 'pairs': pairs,
              'controller_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'workspace': str(a.workspace.resolve()), 'promoted': False}

    def save(state, **details):
        status.update(state=state, updated_unix=time.time(), **details)
        write_json(a.out/'status.json', status)

    workers = 1 if a.mode == 'functional' else 7
    cpus = 'e' if a.mode == 'functional' else 'p'
    reserve = a.reserve_mib if a.reserve_mib is not None else (0 if a.mode == 'functional' else 1024)
    rounds = 1 if a.mode == 'functional' else 4
    try:
        if a.after_status:
            while True:
                gate = json.loads(a.after_status.read_text(encoding='utf-8')) if a.after_status.exists() else {}
                if gate.get('state') == 'failed':
                    raise RuntimeError('Preceding functional gate failed')
                if gate.get('state') == 'complete' and gate.get('semantic_passed'):
                    break
                save('waiting_functional_gate')
                time.sleep(20)
        for round_id in range(rounds):
            order = ['baseline', 'candidate'] if round_id % 2 == 0 else ['candidate', 'baseline']
            for case_file in a.cases:
                seed = str(json.loads(case_file.read_text(encoding='utf-8'))['context']['seed'])
                folders = {}
                for arm in order:
                    reused_attempt = None
                    if a.mode == 'performance' and a.reuse_performance_root:
                        for prior_root in a.reuse_performance_root:
                            prior = json.loads((prior_root/'status.json').read_text())
                            for old_attempt in prior['attempts']:
                                if old_attempt['name'] == f'r{round_id}-{seed}-{arm}' and old_attempt.get('exit_code') == 0:
                                    reused_attempt = dict(old_attempt)
                                    reused = Path(old_attempt.get('source', prior_root/old_attempt['name']))
                                    break
                            if reused_attempt is not None:
                                break
                        if reused_attempt is not None and (reused/'results.jsonl').exists():
                            records = rows(reused)
                            expected = len(json.loads(case_file.read_text(encoding='utf-8'))['cases'])
                            if len(records) == expected and not any('error' in r for r in records):
                                folders[arm] = reused
                                attempt = reused_attempt
                                attempt.update(state='reused', source=str(reused),
                                    reuse_reason='Successful arm; parent-ID/desktop-noise bookkeeping caused false interference')
                                # Preserve raw old records; identify measured/warm
                                # worker PIDs so the final analyzer can remove own
                                # processes from the old background sample.
                                from spire_exact.planning.io import read_json
                                attempt['owned_worker_pids'] = sorted({read_json(path)['pid']
                                    for pattern in ('warm-*/transport.json','case-*/transport.json')
                                    for path in reused.glob(pattern)})
                                attempts.append(attempt)
                                continue
                    if a.mode == 'functional' and a.reuse_functional_root:
                        reused = a.reuse_functional_root/f'r{round_id}-{seed}-{arm}'
                        if (reused/'results.jsonl').exists():
                            records = rows(reused)
                            expected = len(json.loads(case_file.read_text(encoding='utf-8'))['cases'])
                            if len(records) == expected and not any('error' in r for r in records):
                                folders[arm] = reused
                                attempts.append({'name': reused.name, 'state': 'reused', 'source': str(reused)})
                                continue
                    # Waiting for queue controllers also avoids the idle race at
                    # a boundary between two jobs in another agent's queue.
                    # Build/admission happen after the controller's inventory;
                    # retain another GiB for transient host/system allocations.
                    needed = (workers*a.worker_memory_mib + reserve + 1024)*2**20
                    while available_memory() < needed or (a.mode == 'performance' and external_campaigns()):
                        save('waiting_resources', available_mib=available_memory()//2**20,
                             needed_mib=needed//2**20)
                        time.sleep(20)
                    if a.mode == 'performance':
                        # Also check compute processes outside STS2. Timing must
                        # be exclusive even when another project uses P cores.
                        previous = process_sample()
                        while True:
                            time.sleep(10)
                            current = process_sample()
                            busy = busy_external(previous, current)
                            if not busy:
                                break
                            save('waiting_external_cpu', busy=busy)
                            previous = current
                    # Resource demand may change during the CPU wait. Recheck
                    # just before launch; the child also performs strict admission.
                    while available_memory() < needed or (a.mode == 'performance' and external_campaigns()):
                        save('waiting_resources', available_mib=available_memory()//2**20,
                             needed_mib=needed//2**20)
                        time.sleep(20)
                    folder = a.out/f'r{round_id}-{seed}-{arm}'
                    folders[arm] = folder
                    dll = a.baseline if arm == 'baseline' else a.candidate
                    command = [sys.executable, str(a.workspace/'tools/run_fight_bench_job.py'),
                               '--cases', str(case_file.resolve()), '--out', str(folder.resolve()),
                               '--workers', str(workers), '--cpus', cpus,
                               '--worker-memory-mib', str(a.worker_memory_mib), '--reserve-mib', str(reserve),
                               '--runtime-profile', 'server-bounded-large-gen0',
                               '--profile', 'Low', '--nodes', '60000', '--normal-nodes', '10000',
                               '--gate-preset', 'escalate', '--repeat', '1',
                               '--warmup-repeat', '1' if a.mode == 'performance' else '0',
                               '--timeout', '1200', '--solver-dll', str(dll.resolve())]
                    attempt = {'name': folder.name, 'started_unix': time.time(),
                               'command': command, 'state': 'running',
                               'background_allowlist': sorted(BACKGROUND_ALLOWLIST),
                               'background_load_samples': [],
                               'external_campaigns_at_start': external_campaigns() if a.mode == 'performance' else []}
                    attempts.append(attempt)
                    save('running')
                    with (a.out/(folder.name+'.log')).open('w', encoding='utf-8') as stream:
                        child = subprocess.Popen(command, cwd=a.workspace,
                            env=dict(os.environ, PYTHONIOENCODING='utf-8', NO_PROXY='localhost,127.0.0.1,::1'),
                            stdout=stream, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
                        attempt['pid'] = child.pid
                        save('running')
                        interference = []
                        while child.poll() is None:
                            if a.mode == 'performance':
                                interference.extend(external_campaigns())
                            time.sleep(10)
                            if a.mode == 'performance':
                                current = process_sample()
                                attempt['background_load_samples'].append({'unix': time.time(),
                                    'processes': external_load(previous, current, child.pid)})
                                interference.extend(busy_external(previous, current, child.pid))
                                previous = current
                    attempt.update(state='finished', exit_code=child.returncode,
                                   finished_unix=time.time(), interference=interference)
                    save('auditing')
                    if child.returncode or interference:
                        raise RuntimeError('Child failure or external campaign interference: '+folder.name)
                    records = rows(folder)
                    expected = len(json.loads(case_file.read_text(encoding='utf-8'))['cases'])
                    if len(records) != expected or any('error' in r for r in records):
                        raise RuntimeError('Missing/failed native cases: '+folder.name)
                    if a.mode == 'performance':
                        from spire_exact.planning.io import read_json
                        warm_transports = [read_json(path) for path in folder.glob('warm-*/transport.json')]
                        warm_pids = sorted({r['pid'] for r in warm_transports})
                        attempt['warm_worker_pids'] = warm_pids
                        if len(warm_pids) != workers:
                            raise RuntimeError('Not every worker received an explicit warmup')
                        measured = [read_json(path) for path in folder.glob('case-*/transport.json')]
                        if any(r['pid'] not in warm_pids or r['worker_starts'] != 1 for r in measured):
                            raise RuntimeError('Measured worker was restarted/unwarmed')
                baseline, candidate = folders['baseline'], folders['candidate']
                exact = compare_exact(baseline/'results.jsonl', candidate/'results.jsonl', allow_solver_change=True)
                byte_audit = audit_pair(baseline, candidate, allow_solver_change=True)
                write_json(a.out/f'byte-audit-r{round_id}-{seed}.json', byte_audit)
                pair = {'round': round_id, 'seed': seed, 'order': order, 'exact': exact,
                        'full_bytes_passed': byte_audit['passed'],
                        'semantic_passed': exact['equivalent'] and all(all(v for k,v in row.items()
                            if k not in ('case','clock_gates_excluded')) for row in byte_audit['rows']),
                        'clock_gates_excluded': all(row['clock_gates_excluded'] for row in byte_audit['rows']),
                        'baseline': totals(baseline), 'candidate': totals(candidate)}
                pair['ratio'] = pair['candidate']['search_wall']/pair['baseline']['search_wall']
                pairs.append(pair)
                save('pair_complete')
                if not pair['semantic_passed'] or (a.mode == 'performance' and not byte_audit['passed']):
                    raise RuntimeError('Strict equivalence gate failed')
        summed = {arm: sum(x[arm]['search_wall'] for x in pairs) for arm in ['baseline', 'candidate']}
        ratio = summed['candidate']/summed['baseline']
        round_ratios = []
        for index in range(rounds):
            subset = [pair for pair in pairs if pair['round'] == index]
            round_ratios.append(sum(x['candidate']['search_wall'] for x in subset)/sum(x['baseline']['search_wall'] for x in subset))
        save('complete', passed=all(pair['full_bytes_passed'] for pair in pairs),
             semantic_passed=all(pair['semantic_passed'] for pair in pairs),
             clock_gates_excluded=all(pair['clock_gates_excluded'] for pair in pairs),
             search_wall=summed, combined_ratio=ratio,
             round_ratios=round_ratios,
             step_timing_passed=a.mode=='performance' and ratio<=0.97 and all(r<1 for r in round_ratios),
             whole_b1_timing_passed=a.mode=='performance' and ratio<=0.90 and all(r<1 for r in round_ratios),
             timing_claim_allowed=a.mode=='performance')
        write_json(a.out/'completion.json', status)
    except Exception as error:
        save('failed', passed=False, error=str(error))
        write_json(a.out/'completion.json', status)
        raise


if __name__ == '__main__':
    main()
