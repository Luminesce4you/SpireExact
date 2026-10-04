"""Run several tools/run_seed.py jobs one after another (one protocol job at a time on a CPU class).

The queue file is a JSON list; each item is the argument list of one `tools/run_seed.py` call
(without --detach). The launcher puts every run in its own Job with the protocol limits. Start the
controller detached so the queue survives a restart of the application that launched it:

    python tools/run_queue.py --detach experiments/iteration-NNN/queue.json
    python tools/run_queue.py --detach --after experiments/iteration-NNN/queue.log.jsonl 4 experiments/iteration-MMM/queue.json

--after LOG N waits until another queue's log has N finished jobs. A file <queue>.stop next to the
queue file ends the queue after the job that is running. Every finished job appends one line to
<queue>.log.jsonl (exit code, seconds, the launcher's summary line).

--wait-idle holds every job back until no dotnet.exe process is busy (solver workers, builds and tests of
the other agent are all dotnet.exe), so a long queue does not start a job on top of someone else's. The log
line then also carries the seconds waited and the available physical memory at the start of the job.

The controller ignores Ctrl+C and Ctrl+Break (a stray console event ended the iteration-055 queue mid-job;
the job itself has its own console and finished). Stop a queue with the stop file.

--stop-unsolved ends the queue after the first job whose launcher summary does not report a verified win
(a run that did not solve, and also any failed or infrastructure-broken job: look at it before going on).
The log then gets one more line {"index": i, "stopped": ...}.
"""
from pathlib import Path
import json, signal, subprocess, sys, time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def dotnet_cpu_seconds(interval=10):
    """CPU seconds all dotnet.exe processes used over `interval` seconds; a process that ended counts as busy."""
    total = '(Get-Process dotnet -ErrorAction SilentlyContinue | Measure-Object CPU -Sum).Sum'
    script = ('$a=[double]%s; Start-Sleep -Seconds %d; $b=[double]%s; '
              '[math]::Abs($b-$a).ToString([cultureinfo]::InvariantCulture)' % (total, interval, total))
    done = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script], capture_output=True, text=True,
                          creationflags=subprocess.CREATE_NO_WINDOW)
    try: return float(done.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError): return 0.0


def wait_idle(stop):
    """Seconds waited until two readings in a row show under 2 CPU seconds per 10 s; None when the stop file appears."""
    started = time.time(); quiet = 0
    while quiet < 2:
        if stop.exists(): return None
        quiet = quiet + 1 if dotnet_cpu_seconds() < 2 else 0
        if not quiet: time.sleep(20)
    return round(time.time() - started, 1)


def main():
    args = [a for a in sys.argv[1:] if a != '--detach']
    idle = '--wait-idle' in args
    if idle: args.remove('--wait-idle')
    stop_unsolved = '--stop-unsolved' in args
    if stop_unsolved: args.remove('--stop-unsolved')
    after = None
    if '--after' in args:
        at = args.index('--after'); after = (Path(args[at + 1]).resolve(), int(args[at + 2])); del args[at:at + 3]
    queue_file = Path(args[0]).resolve()
    log = queue_file.with_suffix('.log.jsonl')
    if '--detach' in sys.argv[1:]:
        from tools.run_seed import spawn_detached
        extra = (['--wait-idle'] if idle else []) + (['--stop-unsolved'] if stop_unsolved else []) + (['--after', after[0], after[1]] if after else [])
        pid = spawn_detached([sys.executable, Path(__file__).resolve(), *extra, queue_file], queue_file.with_suffix('.controller.log'), ROOT)
        print(json.dumps({'detached_pid': pid, 'queue': str(queue_file), 'log': str(log)})); return
    for name in ('SIGINT', 'SIGBREAK'):
        if hasattr(signal, name): signal.signal(getattr(signal, name), signal.SIG_IGN)
    while after and (not after[0].exists() or sum(1 for line in after[0].open(encoding='utf-8') if line.strip()) < after[1]):
        if queue_file.with_suffix('.stop').exists(): return
        time.sleep(20)
    jobs = json.loads(queue_file.read_text(encoding='utf-8'))
    for index, job in enumerate(jobs):
        if queue_file.with_suffix('.stop').exists():
            with log.open('a', encoding='utf-8') as f: f.write(json.dumps({'index': index, 'skipped': 'stop file present'}) + '\n')
            break
        note = {}
        if idle:
            waited = wait_idle(queue_file.with_suffix('.stop'))
            if waited is None: continue
            from spire_exact.planning.resources import available_memory
            note = {'waited_seconds': waited, 'available_mib': available_memory() // 2 ** 20}
        started = time.time()
        done = subprocess.run([sys.executable, str(ROOT / 'tools/run_seed.py'), *job], cwd=ROOT, capture_output=True, text=True,
                              creationflags=subprocess.CREATE_NO_WINDOW)
        with log.open('a', encoding='utf-8') as f:
            f.write(json.dumps({'index': index, 'job': job, 'exit_code': done.returncode, 'seconds': round(time.time() - started, 1), **note,
                                'stdout': done.stdout[-1500:], 'stderr': done.stderr[-1500:]}, ensure_ascii=False) + '\n')
        if stop_unsolved:
            try: summary = json.loads(done.stdout.strip().splitlines()[-1])
            except (ValueError, IndexError): summary = {}
            if summary.get('verified_win') is not True:
                with log.open('a', encoding='utf-8') as f:
                    f.write(json.dumps({'index': index, 'stopped': 'no verified win (exit %s)' % done.returncode}) + '\n')
                break


if __name__ == '__main__':
    main()
