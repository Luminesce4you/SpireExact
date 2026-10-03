"""Bounded experiment diagnostics, never pruning search states during a run.

Only completed seed directories are eligible. Proofs, reports, action traces,
identities, requests and explicitly pinned regression cases are preserved.
Junctions are inventoried through their real root once, never traversed for delete.
"""
from pathlib import Path
from contextlib import contextmanager
import argparse, json, os, stat, time

GB = 1_000_000_000
TASK_CAP = 150 * GB
ADMISSION_CAP = 110 * GB
JOB_WRITE_CAP = 8 * GB
KEEP_SEEDS = 40
SKIP = {'native', 'spire_exact', 'tools', 'tests', 'runtime', 'vendor', '.git', '__pycache__'}

def linked(path):
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())

def walk(root):
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [n for n in dirs if not linked(Path(base, n))]
        yield Path(base), dirs, files

def logical_bytes(root):
    """Fresh inventory using directory-entry metadata, without per-file reopens.

    Windows scandir already supplies size and reparse metadata. The old Path
    symlink/junction/stat sequence reopened every file three times. No cached
    totals, admission credit or retention/deletion rules are introduced here.
    """
    total = 0
    pending = [os.fspath(root)]
    while pending:
        directory = pending.pop()
        try:
            entries = os.scandir(directory)
        except FileNotFoundError:
            continue  # Concurrent owned writer completed.
        with entries:
            for entry in entries:
                try:
                    info = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_reparse_tag', 0) == getattr(stat, 'IO_REPARSE_TAG_MOUNT_POINT', -1):
                    continue
                if stat.S_ISDIR(info.st_mode):
                    pending.append(entry.path)
                else:
                    total += info.st_size
    return total

def owner_root(root):
    root = Path(root).resolve()
    return root.parent.parent if root.parent.name == 'experiments' and root.name.startswith('frozen-') else root

def config(root):
    path = owner_root(root) / 'storage-policy.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None

def roots_for(root):
    owner = owner_root(root)
    policy = config(root)
    roots = [owner / 'experiments']
    if policy: roots += [Path(x).resolve() for x in policy['artifact_roots']]
    return roots

def completed_seeds(roots):
    seeds = []
    for root in roots:
        for base, dirs, files in walk(root):
            dirs[:] = [n for n in dirs if n not in SKIP]
            if base.name.startswith('seed-'):
                dirs[:] = []
                if 'result.json' in files and not is_active(base):
                    seeds.append(base)
    return sorted(seeds, key=lambda p: (p / 'result.json').stat().st_mtime, reverse=True)

def safe_unlink(path, root):
    """Check the final absolute path AND each component before every deletion."""
    root = Path(root).resolve(strict=True)
    path = Path(path).absolute()
    if path == root or not path.is_relative_to(root) or not path.resolve(strict=True).is_relative_to(root):
        raise ValueError('Retention target escaped its experiment root')
    cursor = path
    while cursor != root:
        if linked(cursor): raise ValueError('Retention refuses reparse/symlink targets')
        cursor = cursor.parent
    size = path.stat().st_size
    path.unlink()
    return size

def is_active(seed):
    return ((seed / '.storage-active.json').exists() or
            any((seed.parent / (seed.name + suffix)).exists() for suffix in ('.active.json', '.job-active.json')))

def prune_seed(seed, root, detailed, apply=False):
    if is_active(seed): return {'skipped_active': True}
    stamp = (seed / 'result.json').stat().st_mtime_ns
    previous = seed / 'retention.json'
    prior = {}
    if previous.exists():
        prior = json.loads(previous.read_text(encoding='utf-8'))
        if prior.get('policy_version') == 1 and prior.get('result_mtime_ns') == stamp and prior.get('detailed_window') == detailed:
            return {'seed': str(seed), 'already_retained': True}
    result = json.loads((seed / 'result.json').read_text(encoding='utf-8-sig'))
    # A representative at greatest reached floor for every diagnostic class.
    reps = {}
    for e in result.get('evaluations', []):
        cls = e.get('classification', 'UNKNOWN')
        key = ((e.get('observation') or {}).get('floor', -1), e.get('actions', 0))
        if cls not in reps or key > reps[cls][0]: reps[cls] = (key, e['label'])
    keep = {x[1] for x in reps.values()}
    keep.add(result.get('best_label'))
    # Every verification attempt is kept, including failed independent replay.
    keep.update(p.name.removeprefix('verify-') for p in seed.glob('verify-*'))
    pinned_seed = (seed / '.retain-detail').exists()
    removed = 0; count = 0; categories = {}
    for ev in seed.glob('eval-*'):
        if not ev.is_dir() or linked(ev): continue
        proof = (seed / ('verify-' + ev.name)).exists()
        pinned = pinned_seed or (ev / '.retain-detail').exists()
        for base, _, files in walk(ev):
            for name in files:
                p = base / name
                parts = p.relative_to(ev).parts
                category = None
                if not proof and 'checkpoints' in parts:
                    category = 'completed_search_checkpoint_cache'
                elif not proof and not pinned and not detailed and ev.name not in keep:
                    if name in {'decision.json', 'decision-evidence.jsonl'} or 'advisor' in parts:
                        category = 'expired_detailed_diagnostics'
                # Do not remove partial failure journals without an intact decision.
                elif not proof and name == 'decision-evidence.jsonl' and (base / 'decision.json').is_file():
                    category = 'duplicate_evidence_journal'
                if category:
                    size = safe_unlink(p, root) if apply else p.stat().st_size
                    removed += size; count += 1
                    categories[category] = categories.get(category, 0) + size
    report = {'seed': str(seed), 'time': time.time(), 'detailed_window': detailed,
              'policy_version': 1, 'result_mtime_ns': stamp,
              'removed_logical_bytes': removed, 'removed_files': count, 'categories': categories,
              'cumulative_removed_logical_bytes': prior.get('cumulative_removed_logical_bytes', prior.get('removed_logical_bytes', 0)) + removed,
              'cumulative_removed_files': prior.get('cumulative_removed_files', prior.get('removed_files', 0)) + count,
              'retained_representatives': sorted(x for x in keep if x),
              'checkpoint_reuse_available': False,
              'scope': 'Storage retention only; no change to result or search bounds'}
    if apply:
        (seed / 'retention.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report

@contextmanager
def management_lock(root):
    path = owner_root(root) / 'experiments/storage-retention.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as f:
        f.seek(0, 2)
        if not f.tell(): f.write(b'0'); f.flush()
        f.seek(0)
        if os.name == 'nt':
            import msvcrt
            until = time.monotonic() + 600
            while True:
                try: msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1); break
                except OSError:
                    if time.monotonic() > until: raise
                    time.sleep(.2)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try: yield
        finally:
            f.seek(0)
            if os.name == 'nt': msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else: fcntl.flock(f.fileno(), fcntl.LOCK_UN)

def sweep(root, apply=False, keep_seeds=KEEP_SEEDS):
    with management_lock(root): return _sweep(root, apply, keep_seeds)

def _sweep(root, apply=False, keep_seeds=KEEP_SEEDS):
    roots = roots_for(root)
    seeds = completed_seeds(roots)
    rows = []
    for i, seed in enumerate(seeds):
        boundary = next(r.resolve() for r in roots if seed.resolve().is_relative_to(r.resolve()))
        rows.append(prune_seed(seed, boundary, i < keep_seeds, apply))
    report = {'applied': apply, 'keep_recent_completed_seeds': keep_seeds,
              'task_cap_bytes': TASK_CAP, 'removed_logical_bytes': sum(r.get('removed_logical_bytes', 0) for r in rows),
              'removed_files': sum(r.get('removed_files', 0) for r in rows), 'seeds': rows}
    folder = owner_root(root) / 'experiments/storage-retention'
    folder.mkdir(exist_ok=True)
    # Bounded management log: latest 10 sweeps, in addition to per-seed summaries.
    for i in range(8, -1, -1):
        old = folder / f'sweep-{i}.json'
        if old.exists(): old.replace(folder / f'sweep-{i+1}.json')
    (folder / 'sweep-0.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report

def admission(root):
    """Run before a batch: 40 GB reserve covers four 8 GB owned write budgets."""
    policy = config(root)
    if not policy: return {}
    usage = {str(Path(p).resolve()): logical_bytes(Path(p).resolve()) for p in policy['artifact_roots']}
    total = sum(usage.values())
    if total > ADMISSION_CAP:
        raise RuntimeError(f'STORAGE_ADMISSION_DENIED: task artifacts {total} exceed {ADMISSION_CAP}; retention required')
    return {'artifact_bytes': total, 'task_cap_bytes': TASK_CAP,
            'admission_cap_bytes': ADMISSION_CAP, 'per_job_write_cap_bytes': JOB_WRITE_CAP}

def acquire_job_slot(root):
    """Four process-held leases across every frozen workspace; OS releases on crash."""
    folder = owner_root(root) / 'experiments/storage-retention'
    folder.mkdir(exist_ok=True)
    if os.name != 'nt': raise RuntimeError('Job leases require Windows in this runner')
    import msvcrt
    while True:
        for i in range(4):
            f = (folder / f'job-slot-{i}.lock').open('a+b')
            f.seek(0, 2)
            if not f.tell(): f.write(b'0'); f.flush()
            f.seek(0)
            try: msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1); return f
            except OSError: f.close()
        time.sleep(.25)

@contextmanager
def active_seed(seed):
    # Marker is outside the empty solver directory. Retention also checks it below.
    marker = seed.parent / (seed.name + '.active.json')
    marker.write_text(json.dumps({'pid': os.getpid(), 'time': time.time()}))
    try: yield
    finally:
        marker.unlink(missing_ok=True)
        # The caller has joined the owned child process, including hard exits.
        (seed.parent / (seed.name + '.job-active.json')).unlink(missing_ok=True)

class RollingConsole:
    """Only human-readable console logs; never protocol/evidence/state files."""
    def __init__(self, path, max_bytes=4*1024*1024, backups=2):
        self.path = Path(path); self.max_bytes = max_bytes; self.backups = backups
        self.stream = self.path.open('wb'); self.size = 0

    def write(self, data):
        while data:
            if self.size == self.max_bytes:
                self.stream.close()
                for i in range(self.backups, 0, -1):
                    old = self.path if i == 1 else Path(str(self.path) + f'.{i-1}')
                    if old.exists(): old.replace(Path(str(self.path) + f'.{i}'))
                self.stream = self.path.open('wb'); self.size = 0
            chunk = data[:self.max_bytes-self.size]
            self.stream.write(chunk); self.stream.flush()
            self.size += len(chunk); data = data[len(chunk):]

    def close(self): self.stream.close()

def main():
    p = argparse.ArgumentParser(); p.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument('--apply', action='store_true'); a = p.parse_args()
    report = sweep(a.root, a.apply)
    print(json.dumps({k: v for k, v in report.items() if k != 'seeds'}, indent=2))

if __name__ == '__main__': main()
