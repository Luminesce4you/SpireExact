"""Record-replay acceptance for the planner refactor.

Feeds the retained worker results of a recorded real-game run back into THIS
workspace's planner, in their recorded completion order, and requires every
request the planner sends to be byte-identical (canonical JSON) to the recorded
request: up to the winning evaluation, or to the end of the recording for a run
that stopped on its time budget. No game process is started and nothing is
written next to the recording.

Generalised from P5plus-f1-card-switches/experiments/iteration-105/replay_check.py
(3/3 identical on frozen-i054a-shm, 2026-10-08). The comparison is unchanged;
added: runs without a win, panels, parallel jobs, memory guard, planner hash.

  python tools/replay_golden.py --tag NAME --panel quick [--jobs 4]
  python tools/replay_golden.py --tag NAME RUN_DIR [RUN_DIR ...]

The planner is snapshotted at launch; each run is replayed in its own process
from that snapshot, pinned to E cores at below-normal priority while real jobs use the P cores (--cores p lifts the pin when idle).
"""
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import argparse
import ctypes
import datetime
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
ART = Path(os.environ.get('SPIRE_ARTIFACT_ROOT', str(ROOT / 'outputs/research')))
REPORTS = ART / 'refactor-i054a-shm' / 'replay'
E_CORES = 0x3FFF0000
# Interpreter of the iteration-104 / 105 launchers (P5plus-f1-card-switches/
# experiments/iteration-105/common.py): Python 3.12.14.
RECORDING_PYTHON = Path(sys.executable)
FAILED = ('SEARCH_CANCELLED', 'TIMEOUT', 'RESOURCE_LIMIT', 'UNKNOWN', 'NATIVE_CRASH')
# Diverse and fast: root win, early wins, gate-retry wins, macro_failure win,
# a time-budget run, and the three runs of the original 105 replay check.
QUICK = ['iteration-105-ablation/ab105-A-1583616147',
         'iteration-104-i054a-r20b/i054a-shm-r20b-365003757',
         'iteration-105-ablation/ab105-A-1489631144',
         'iteration-105-ablation/ab105-A-1244814671',
         'iteration-105-ablation/ab105-A2-36219659',
         'iteration-105-ablation/ab105-A-703392077',
         'iteration-105-ablation/ab105-A-1946805590',
         'iteration-104-i054a-r20b/i054a-shm-r20b-563377766',
         'iteration-104-i054a-r20b/i054a-shm-r20b-1993574835',
         'iteration-104-i054a-r20b/i054a-shm-r20b-2010212533']


class Divergence(Exception):
    pass


class EndOfRecording(Exception):
    pass


def now():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f'.tmp-{os.getpid()}')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    os.replace(temp, path)


def rows(path):
    with Path(path).open(encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def serial_of(label):
    return int(label.split('-')[1])


def planner_sha(workspace):
    h = hashlib.sha256()
    for path in sorted((workspace / 'spire_exact').rglob('*.py')):
        h.update(path.relative_to(workspace).as_posix().encode() + b'\0' + path.read_bytes() + b'\0')
    return h.hexdigest()


def pin(cores):
    if os.name != 'nt' or cores == 'p':
        return
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.GetCurrentProcess.restype = ctypes.c_void_p
    k.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    k.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    h = k.GetCurrentProcess()
    if not k.SetProcessAffinityMask(h, E_CORES) or not k.SetPriorityClass(h, 0x00004000):
        raise ctypes.WinError(ctypes.get_last_error())


def peak_mib():
    class Counters(ctypes.Structure):
        _fields_ = [('cb', ctypes.c_uint32), ('faults', ctypes.c_uint32)] + [(n, ctypes.c_size_t) for n in (
            'peak_ws', 'ws', 'qpp', 'qp', 'qnpp', 'qnp', 'pagefile', 'peak_pagefile')]
    c = Counters()
    c.cb = ctypes.sizeof(c)
    k = ctypes.WinDLL('kernel32')
    k.GetCurrentProcess.restype = ctypes.c_void_p
    psapi = ctypes.WinDLL('psapi')
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32]
    psapi.GetProcessMemoryInfo(k.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return round(c.peak_ws / 2**20)


def available_mib():
    class Status(ctypes.Structure):
        _fields_ = [('length', ctypes.c_uint32), ('load', ctypes.c_uint32)] + [(s, ctypes.c_uint64) for s in
            ('total_phys', 'avail_phys', 'total_page', 'avail_page', 'total_virtual', 'avail_virtual', 'extended')]
    s = Status()
    s.length = ctypes.sizeof(s)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
    return s.avail_phys / 2**20


def decision(seed_dir, label):
    data = seed_dir / label / 'data'
    if (data / 'decision.json.gz').exists():
        with gzip.open(data / 'decision.json.gz', 'rt', encoding='utf-8') as f:
            return json.load(f)
    return read(data / 'decision.json')


def recording(run):
    manifest = read(run / 'validation-manifest.json')
    seed_dir = run / ('seed-' + str(manifest['seed']))
    original = read(seed_dir / 'result.json')
    ledger = rows(seed_dir / 'evaluations.jsonl')
    winner = next((serial_of(r['label']) for r in ledger if r.get('classification') == 'NATIVE_WIN_CANDIDATE'), None)
    return manifest, seed_dir, original, ledger, winner


# Volatile fields of the final result.json: wall and CPU time, pool counters.
VOLATILE = ('elapsed_seconds', 'pool_stats')
VOLATILE_RECORD = ('coordinator_cpu_seconds', 'runtime')


def report_projection(result):
    value = {k: v for k, v in result.items() if k not in VOLATILE}
    value['evaluations'] = [{k: v for k, v in r.items() if k not in VOLATILE_RECORD} for r in result.get('evaluations', [])]
    return json.loads(canonical(value))


def compare_result(actual, expected):
    """Final run report identical apart from volatile fields: top-level keys
    (and search_metrics keys) that differ."""
    a, e = report_projection(actual), report_projection(expected)
    keys = sorted(k for k in set(a) | set(e) if canonical(a.get(k)) != canonical(e.get(k)))
    metrics = sorted(k for k in set(a.get('search_metrics', {})) | set(e.get('search_metrics', {}))
                     if canonical(a.get('search_metrics', {}).get(k)) != canonical(e.get('search_metrics', {}).get(k)))
    return {'identical': not keys, 'keys': keys, 'search_metrics_keys': metrics}


def replay_one(run, report_path, cores, code):
    """Child process: replay one recorded run against the planner snapshot `code`."""
    pin(cores)
    started_at = time.perf_counter()
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(code), str(ROOT)]
    os.chdir(ROOT)
    os.environ.update(OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1')
    from spire_exact.planning import search
    from spire_exact.native import input_fingerprint
    from spire_exact.planning.pool import WorkerError
    if Path(search.__file__).resolve().parents[2] != Path(code).resolve():
        raise RuntimeError(f'planner imported from {search.__file__}, not from the snapshot {code}')

    manifest, seed_dir, original, ledger, winner = recording(run)
    indexed = {serial_of(r['label']): r for r in ledger}
    stop = original.get('stop_reason')
    if winner is None and stop != 'time_budget':
        raise ValueError(f'Recording neither won nor reached its time budget: {stop}')
    # Serials whose requests must match: up to the win, else the whole recording.
    last = winner if winner is not None else max(indexed)
    first_label = indexed[min(indexed)]['label']
    first_request = read(seed_dir / first_label / 'request.json')
    identity = read(seed_dir / first_label / 'data/identity.json')
    cfg = search.SearchConfig(**original['configuration'])
    clock = [0.0]
    next_record = [0]
    comparisons = []
    first_difference = []
    tail = {}

    # Hooks: recorded clock, no snapshot/ledger writes, recorded verification.
    if hasattr(search.Evaluator, 'clock'):
        search.Evaluator.clock = staticmethod(lambda: clock[0])
    if hasattr(search, 'perf_counter'):
        search.perf_counter = lambda: clock[0]

    def record(self, row):
        if next_record[0] >= len(ledger) or row['label'] != ledger[next_record[0]]['label']:
            expected = ledger[next_record[0]]['label'] if next_record[0] < len(ledger) else '<end>'
            raise Divergence(f'Record completion order differs: actual {row["label"]}, expected {expected}')
        old = ledger[next_record[0]]
        next_record[0] += 1
        clock[0] = max(clock[0], float(old.get('completed_wall_seconds') or 0))
        row['completed_wall_seconds'] = old.get('completed_wall_seconds')
        row.setdefault('runtime', {})
        self.records.append(row)
    search.Evaluator.record = record
    search.Evaluator.verify_win = lambda self, result, label: {'recorded_verified_win': label}

    class PlaybackFuture(Future):
        def __init__(self, row):
            # Payloads are read only when absorbed: preloading a 56-request
            # window used nearly 3 GiB in the original check.
            super().__init__()
            self.set_result(None)
            self.row = row

        def result(self, *args, **kwargs):
            clock[0] = max(clock[0], float(self.row.get('completed_wall_seconds') or 0))
            label = self.row['label']
            if self.row.get('classification') in FAILED:
                raise WorkerError(self.row.get('reason') or self.row['classification'])
            return decision(seed_dir, label), read(seed_dir / label / 'data/identity.json')

        def done(self):
            # Only the next recorded completion is ready: initial batch
            # completions mutate the checkpoint archive in that order.
            if next_record[0] >= len(ledger):
                raise EndOfRecording('Planner waits for a completion beyond the recording')
            return ledger[next_record[0]]['label'] == self.row['label']

    class Pool:
        def __init__(self):
            self.resources = SimpleNamespace(workers=original['resources']['workers'],
                                             as_dict=lambda: original['resources'])
            self.runtime = original.get('native_runtime')
            data = ROOT / 'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
            self.inputs = {**input_fingerprint(data), 'native_runtime': self.runtime}
            self.stamp = {'host_sha256': identity['host_sha256']}
            self.stats = {}
            self.serial = 0

        def validate_inputs(self, request):
            serial = self.serial
            self.serial += 1
            if serial > last:
                return
            if serial not in indexed:
                raise Divergence('Missing retained evaluation ' + str(serial))
            row = indexed[serial]
            folder = seed_dir / row['label']
            expected = read(folder / 'cached.json')['request'] if row.get('cache_hit') else read(folder / 'request.json')
            actual = deepcopy(request)
            # NativePool.run supplies exactly these transport fields after the evaluator.
            if not row.get('cache_hit'):
                actual.update(command='replay', compact=True, out=expected['out'])
                expected = deepcopy(expected)
                expected.pop('checkpoint', None)
            self.compare(actual, expected, serial, 'dispatch before checkpoint lookup')

        def compare(self, actual, expected, serial, phase):
            if canonical(actual) != canonical(expected):
                diff = {'serial': serial, 'label': indexed[serial]['label'], 'phase': phase,
                        'changed_keys': sorted(k for k in set(actual) | set(expected) if actual.get(k) != expected.get(k))}
                first_difference.append(diff)
                write(Path(report_path).with_suffix('.first-difference.json'),
                      {'difference': diff, 'actual': actual, 'expected': expected})
                raise Divergence(json.dumps(diff))
            comparisons.append(serial)

        def submit(self, request, out, timeout):
            serial = serial_of(out.name)
            row = indexed.get(serial)
            if row is None:
                # A time-budget recording may end one dispatch earlier than the
                # replay: the replay clock is the last recorded completion, the
                # real clock was later by the absorb time.
                if winner is None and serial > last and clock[0] >= cfg.seconds - 30:
                    tail.update(serial=serial, clock=clock[0], deadline=cfg.seconds)
                    raise EndOfRecording('Dispatch after the recorded end, within 30 s of the deadline')
                raise Divergence('Planner scheduled unrecorded serial ' + str(serial))
            if serial <= last:
                expected = read(seed_dir / row['label'] / 'request.json')
                actual = {**request, 'command': 'replay', 'compact': True, 'out': expected['out']}
                self.compare(actual, expected, serial, 'full request with recorded destination')
            return PlaybackFuture(row)

        def cancel_pending(self):
            pass

    report = {'run': str(run), 'seed': manifest['seed'], 'solver_seed': manifest.get('solver_seed'),
              'recorded_stop': stop, 'winning_serial': winner, 'last_compared_serial': last,
              'ledger_rows': len(ledger), 'workspace': str(ROOT), 'planner_sha256': planner_sha(Path(code)),
              'harness_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'python': sys.version.split()[0], 'started': now(), 'passed': False}
    sandbox = Path(tempfile.mkdtemp(prefix='replay-', dir=Path(report_path).parent))
    try:
        try:
            result = search.solve(original['context'], sandbox / 'out', Pool(), cfg, advisor=first_request['advisor'])
            report['replayed_stop'] = result.get('stop_reason')
            report['result_report'] = compare_result(result, original)
        except EndOfRecording as exc:
            report['end_of_recording'] = str(exc)
        checked = set(comparisons)
        missing = sorted(set(indexed) & set(range(last + 1)) - checked)
        report.update(passed=not missing and (report.get('result_report') or {}).get('identical', True),
                      compared_serials=len(checked), compared_requests=len(comparisons),
                      absorbed_records=next_record[0], missing_serials=missing[:50], tail=tail or None)
        if stop == 'time_budget' and winner is None and report.get('replayed_stop') not in (None, 'time_budget'):
            report.update(passed=False, error='Replayed stop reason differs from the recording')
    except Exception as exc:
        report.update(error=repr(exc), traceback=traceback.format_exc()[-4000:],
                      first_difference=first_difference[0] if first_difference else None,
                      compared_serials=len(set(comparisons)), compared_requests=len(comparisons),
                      absorbed_records=next_record[0])
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)
    report.update(completed=now(), seconds=round(time.perf_counter() - started_at, 1), peak_mib=peak_mib())
    write(report_path, report)


def full_panel():
    runs = sorted((ART / 'iteration-104-i054a-r20b').glob('i054a-shm-r20b-*'))
    runs += sorted((ART / 'iteration-105-ablation').glob('ab105-A*'))
    keep = []
    for run in runs:
        try:
            manifest, seed_dir, original, ledger, winner = recording(run)
        except (OSError, ValueError, KeyError):
            continue
        if manifest.get('version') not in (None, 'i054a-shm-81299b3c63cac83d'):
            continue
        if winner is not None or original.get('stop_reason') == 'time_budget':
            keep.append(run)
    return keep


def main():
    if sys.argv[1:2] == ['_one']:
        replay_one(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4], Path(sys.argv[5]))
        return
    parser = argparse.ArgumentParser()
    parser.add_argument('runs', nargs='*')
    parser.add_argument('--tag', required=True)
    parser.add_argument('--panel', choices=('quick', 'full'))
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--cores', choices=('e', 'p'), default='e')
    parser.add_argument('--min-free-mib', type=int, default=6144)
    parser.add_argument('--code', type=Path, default=ROOT, help='workspace whose spire_exact is replayed (default: this one)')
    parser.add_argument('--python', type=Path, default=RECORDING_PYTHON if RECORDING_PYTHON.is_file() else Path(sys.executable),
                        help='interpreter that made the recordings; default is this Python, override with --python')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', args.tag):
        parser.error('--tag must be a plain run identifier without directory components')
    runs = [Path(r) for r in args.runs]
    if args.panel == 'quick':
        runs += [ART / r for r in QUICK]
    elif args.panel == 'full':
        runs += full_panel()
    if not runs:
        parser.error('no runs')
    pin(args.cores)
    out = REPORTS / args.tag
    out.mkdir(parents=True, exist_ok=True)
    # Children import this snapshot, so the workspace may be edited meanwhile.
    code = out / 'planner'
    if code.exists():
        parser.error('this replay tag already has a planner snapshot; use a new --tag')
    shutil.copytree(args.code / 'spire_exact', code / 'spire_exact', ignore=shutil.ignore_patterns('__pycache__'))
    # Longest first so that the slowest replay does not start last.
    runs.sort(key=lambda r: -sum(1 for _ in (r / ('seed-' + str(read(r / 'validation-manifest.json')['seed'])) / 'evaluations.jsonl').open(encoding='utf-8')))

    def launch(run):
        while available_mib() < args.min_free_mib:
            time.sleep(20)
        report = out / (run.name + '.json')
        with (out / (run.name + '.log')).open('w', encoding='utf-8') as log:
            subprocess.run([str(args.python), str(Path(__file__).resolve()), '_one', str(run), str(report), args.cores, str(code)],
                           stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW | 0x00004000,
                           env={**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONDONTWRITEBYTECODE': '1'})
        if not report.exists():
            return {'run': str(run), 'passed': False, 'error': 'child wrote no report; see log'}
        r = read(report)
        print(f"{'PASS' if r['passed'] else 'FAIL'} {run.name} compared={r.get('compared_serials')} "
              f"last={r.get('last_compared_serial')} report={(r.get('result_report') or {}).get('identical')} "
              f"{r.get('seconds')}s {r.get('peak_mib')}MiB "
              f"{r.get('error') or ''}", flush=True)
        return r

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        reports = list(pool.map(launch, runs))
    summary = {'tag': args.tag, 'completed': now(), 'code': str(args.code), 'planner_sha256': planner_sha(code),
               'python': sorted({r.get('python') for r in reports if r.get('python')}),
               'runs': len(reports), 'passed': sum(bool(r.get('passed')) for r in reports),
               'failed': [r['run'] for r in reports if not r.get('passed')],
               'compared_serials': sum(r.get('compared_serials') or 0 for r in reports)}
    write(out / 'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False))
    raise SystemExit(0 if summary['passed'] == summary['runs'] else 1)


if __name__ == '__main__':
    main()
