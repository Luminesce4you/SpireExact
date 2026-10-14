"""Run several tools/run_seed.py jobs one after another (one protocol job at a time on a CPU class).

The queue file is a JSON list; each item is the argument list of one `tools/run_seed.py` call
(without --detach). The launcher puts every run in its own Job with the protocol limits. Start the
controller detached so the queue survives a restart of the application that launched it:

    python tools/run_queue.py --detach experiments/iteration-NNN/queue.json
    python tools/run_queue.py --detach --after experiments/iteration-NNN/queue.log.jsonl 4 experiments/iteration-MMM/queue.json

--after LOG N waits until another queue's log has N finished jobs. A file <queue>.stop next to the
queue file ends the queue after the job that is running. Every finished job appends one line to
<queue>.log.jsonl (exit code, seconds, the launcher's summary line).
"""
from pathlib import Path
import json, subprocess, sys, time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    args = [a for a in sys.argv[1:] if a != '--detach']
    after = None
    if '--after' in args:
        at = args.index('--after'); after = (Path(args[at + 1]).resolve(), int(args[at + 2])); del args[at:at + 3]
    queue_file = Path(args[0]).resolve()
    log = queue_file.with_suffix('.log.jsonl')
    if '--detach' in sys.argv[1:]:
        from tools.run_seed import spawn_detached
        extra = ['--after', after[0], after[1]] if after else []
        pid = spawn_detached([sys.executable, Path(__file__).resolve(), *extra, queue_file], queue_file.with_suffix('.controller.log'), ROOT)
        print(json.dumps({'detached_pid': pid, 'queue': str(queue_file), 'log': str(log)})); return
    while after and (not after[0].exists() or sum(1 for line in after[0].open(encoding='utf-8') if line.strip()) < after[1]):
        if queue_file.with_suffix('.stop').exists(): return
        time.sleep(20)
    jobs = json.loads(queue_file.read_text(encoding='utf-8'))
    for index, job in enumerate(jobs):
        if queue_file.with_suffix('.stop').exists():
            with log.open('a', encoding='utf-8') as f: f.write(json.dumps({'index': index, 'skipped': 'stop file present'}) + '\n')
            break
        started = time.time()
        done = subprocess.run([sys.executable, str(ROOT / 'tools/run_seed.py'), *job], cwd=ROOT, capture_output=True, text=True,
                              creationflags=subprocess.CREATE_NO_WINDOW)
        with log.open('a', encoding='utf-8') as f:
            f.write(json.dumps({'index': index, 'job': job, 'exit_code': done.returncode, 'seconds': round(time.time() - started, 1),
                                'stdout': done.stdout[-1500:], 'stderr': done.stderr[-1500:]}, ensure_ascii=False) + '\n')


if __name__ == '__main__':
    main()
