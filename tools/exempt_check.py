"""No-run rule for a treatment arm (PLAN-v2 R2a point 2).

For each recorded control run (control_library.py, eligible rows), replays the
TREATMENT configuration (the recording's configuration plus plugins and
overrides) against the recorded worker results, exactly as
tools/replay_golden.py does for the default configuration, but stops at the
first request that differs instead of failing:

- no difference up to the recording's winning serial (or, for a time-budget
  recording, to its end): the treatment run would have been the same run, so
  its result is the control's and it need not be run (verdict `exempt`);
- a difference at serial d: the treatment needs a real run, or a fork from d
  (PLAN-v2 R2b). `fork_seconds` is the recorded clock at d.

Each plugin also declares its scope statically (Plugin.reaches(result.json)).
A run the replay finds diverging although no plugin reaches it is a rule
violation: that plugin's reaches() is wrong (`rule_violations` in the summary).
The dynamic replay is the verdict; reaches() is checked, not trusted.

No game process is started; children are pinned to E cores like replay_golden.

  python tools/exempt_check.py --library LIB.json --tag NAME --plugin p2-permuted-predictor [--set key=json] [--capability CAP] [--role compare] [--jobs 4]

--capability adds a host capability to the replayed build stamp (a plugin such
as p1-f2-reroll refuses to attach without it). The verdict then holds for a
host that has that capability and is otherwise the recorded host; the summary
records it.
"""
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent))
from replay_golden import (ART, FAILED, RECORDING_PYTHON, ROOT, available_mib, canonical, decision, now, peak_mib, pin,  # noqa: E402
                           planner_sha, read, recording, serial_of, write)

OUT = ART / 'refactor-i054a-shm' / 'exempt'


class Divergence(Exception):
    pass


class EndOfRecording(Exception):
    pass


def treatment_of(args):
    return {'plugins': list(args.plugin), 'overrides': {k: json.loads(v) for k, v in (s.split('=', 1) for s in args.set)},
            'capabilities': list(args.capability)}


def check_one(run, report_path, code, treatment_path):
    pin('e')
    started_at = time.perf_counter()
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(code), str(ROOT)]
    os.chdir(ROOT)
    os.environ.update(OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1')
    from spire_exact.planning import search
    from spire_exact.native import input_fingerprint
    from spire_exact.planning.pool import WorkerError
    from spire_exact.planning.plugins import load, register_tables
    if Path(search.__file__).resolve().parents[2] != Path(code).resolve():
        raise RuntimeError(f'planner imported from {search.__file__}, not from the snapshot {code}')
    treatment = read(treatment_path)

    manifest, seed_dir, original, ledger, winner = recording(run)
    indexed = {serial_of(r['label']): r for r in ledger}
    stop = original.get('stop_reason')
    last = winner if winner is not None else max(indexed)
    first_label = indexed[min(indexed)]['label']
    first_request = read(seed_dir / first_label / 'request.json')
    identity = read(seed_dir / first_label / 'data/identity.json')
    unknown = sorted(set(treatment['overrides']) - set(search.SearchConfig.__dataclass_fields__))
    if unknown:
        raise ValueError('unknown SearchConfig field(s): ' + ', '.join(unknown))
    cfg = search.SearchConfig(**{**original['configuration'], **treatment['overrides']})
    plugins = load(treatment['plugins'])
    register_tables(plugins)
    clock = [0.0]
    next_record = [0]
    compared = set()
    divergence = []

    def diverge(serial, phase, **extra):
        info = {'serial': serial, 'label': indexed[serial]['label'] if serial in indexed else None,
                'phase': phase, 'fork_seconds': round(clock[0], 3), **extra}
        divergence.append(info)
        raise Divergence(json.dumps(info))

    if hasattr(search.Evaluator, 'clock'):
        search.Evaluator.clock = staticmethod(lambda: clock[0])
    if hasattr(search, 'perf_counter'):
        search.perf_counter = lambda: clock[0]

    def record(self, row):
        if next_record[0] >= len(ledger) or row['label'] != ledger[next_record[0]]['label']:
            expected = ledger[next_record[0]]['label'] if next_record[0] < len(ledger) else '<end>'
            diverge(serial_of(row['label']), 'completion order', expected=expected)
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
            if treatment['capabilities']:
                self.stamp['capabilities'] = list(treatment['capabilities'])
            self.stats = {}
            self.serial = 0

        def validate_inputs(self, request):
            serial = self.serial
            self.serial += 1
            if serial > last:
                return
            if serial not in indexed:
                diverge(serial, 'dispatch of an unrecorded serial')
            row = indexed[serial]
            folder = seed_dir / row['label']
            expected = read(folder / 'cached.json')['request'] if row.get('cache_hit') else read(folder / 'request.json')
            actual = deepcopy(request)
            if not row.get('cache_hit'):
                actual.update(command='replay', compact=True, out=expected['out'])
                expected = deepcopy(expected)
                expected.pop('checkpoint', None)
            self.compare(actual, expected, serial, 'dispatch before checkpoint lookup')

        def compare(self, actual, expected, serial, phase):
            if canonical(actual) != canonical(expected):
                keys = sorted(k for k in set(actual) | set(expected) if actual.get(k) != expected.get(k))
                write(Path(report_path).with_suffix('.first-difference.json'),
                      {'serial': serial, 'phase': phase, 'actual': actual, 'expected': expected})
                diverge(serial, phase, changed_keys=keys)
            compared.add(serial)

        def submit(self, request, out, timeout):
            serial = serial_of(out.name)
            row = indexed.get(serial)
            if row is None:
                if serial > last:
                    raise EndOfRecording('Dispatch after the last compared serial')
                diverge(serial, 'scheduled an unrecorded serial')
            if serial <= last:
                expected = read(seed_dir / row['label'] / 'request.json')
                actual = {**request, 'command': 'replay', 'compact': True, 'out': expected['out']}
                self.compare(actual, expected, serial, 'full request with recorded destination')
            return PlaybackFuture(row)

        def cancel_pending(self):
            pass

    control_seconds = (indexed[winner].get('completed_wall_seconds') if winner is not None
                       else float(manifest.get('wall_cap_seconds') or cfg.seconds))
    report = {'run': str(run), 'seed': manifest['seed'], 'solver_seed': manifest.get('solver_seed'),
              'recorded_stop': stop, 'winning_serial': winner, 'last_compared_serial': last,
              'control_seconds': control_seconds, 'treatment': treatment,
              'reaches': {p.name: bool(p.reaches(original)) for p in plugins},
              'planner_sha256': planner_sha(Path(code)),
              'harness_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'python': sys.version.split()[0], 'started': now()}
    sandbox = Path(tempfile.mkdtemp(prefix='exempt-', dir=Path(report_path).parent))
    try:
        try:
            result = search.solve(original['context'], sandbox / 'out', Pool(), cfg, advisor=first_request['advisor'],
                                  plugins=plugins)
            report['replayed_stop'] = result.get('stop_reason')
        except EndOfRecording as exc:
            report['end_of_recording'] = str(exc)
        except Divergence:
            pass
        missing = sorted(set(indexed) & set(range(last + 1)) - compared)
        if divergence:
            d = divergence[0]
            report.update(verdict='diverges', divergence=d,
                          fork_fraction=round(d['fork_seconds'] / control_seconds, 4) if control_seconds else None)
        elif missing:
            report.update(verdict='error', error=f'{len(missing)} recorded serials up to {last} were never dispatched',
                          missing_serials=missing[:50])
        else:
            report['verdict'] = 'exempt'
        report['compared_serials'] = len(compared)
        report['absorbed_records'] = next_record[0]
    except Exception as exc:
        report.update(verdict='error', error=repr(exc), traceback=traceback.format_exc()[-4000:])
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)
    if report['verdict'] == 'diverges' and report['reaches'] and not any(report['reaches'].values()):
        report['rule_violation'] = 'diverged although no plugin reaches this run'
    report.update(completed=now(), seconds=round(time.perf_counter() - started_at, 1), peak_mib=peak_mib())
    write(report_path, report)


def main():
    if sys.argv[1:2] == ['_one']:
        check_one(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]), Path(sys.argv[5]))
        return
    parser = argparse.ArgumentParser()
    parser.add_argument('runs', nargs='*', help='run directories (default: eligible rows of --library)')
    parser.add_argument('--library', type=Path)
    parser.add_argument('--role', default='compare', help='library role to check (compare = solver_seed 271828)')
    parser.add_argument('--tag', required=True)
    parser.add_argument('--plugin', action='append', default=[])
    parser.add_argument('--set', action='append', default=[], help='SearchConfig override key=JSON value')
    parser.add_argument('--capability', action='append', default=[])
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--min-free-mib', type=int, default=6144)
    parser.add_argument('--python', type=Path, default=RECORDING_PYTHON if RECORDING_PYTHON.is_file() else Path(sys.executable),
                        help='interpreter of the replays: the one that made the recordings (see replay_golden.py)')
    args = parser.parse_args()
    if not args.plugin and not args.set:
        parser.error('no treatment: give --plugin and / or --set (the default configuration is replay_golden)')
    runs = [Path(r) for r in args.runs]
    host_keys = set()
    if args.library:
        library = read(args.library)
        rows = [r for r in library['runs'] if r['control_eligible'] and r['role'] == args.role]
        if not runs:   # explicit RUN_DIRs restrict the check; the library still gives the host keys
            runs = [Path(r['path']) for r in rows]
        host_keys = {r['host_key'] for r in rows}
    if not runs:
        parser.error('no runs (no eligible library rows and no run directories)')
    pin('e')
    out = OUT / args.tag
    out.mkdir(parents=True, exist_ok=True)
    treatment = treatment_of(args)
    write(out / 'treatment.json', treatment)
    code = out / 'planner'
    if code.exists():
        shutil.rmtree(code)
    shutil.copytree(ROOT / 'spire_exact', code / 'spire_exact', ignore=shutil.ignore_patterns('__pycache__'))

    def launch(run):
        while available_mib() < args.min_free_mib:
            time.sleep(20)
        report = out / (run.name + '.json')
        with (out / (run.name + '.log')).open('w', encoding='utf-8') as log:
            subprocess.run([str(args.python), str(Path(__file__).resolve()), '_one', str(run), str(report), str(code),
                            str(out / 'treatment.json')], stdout=log, stderr=subprocess.STDOUT,
                           creationflags=subprocess.CREATE_NO_WINDOW | 0x00004000,
                           env={**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONDONTWRITEBYTECODE': '1'})
        if not report.exists():
            return {'run': str(run), 'verdict': 'error', 'error': 'child wrote no report; see log'}
        r = read(report)
        d = r.get('divergence') or {}
        print(f"{r['verdict']:8} {run.name} reaches={r.get('reaches')} serial={d.get('serial')} "
              f"fork={d.get('fork_seconds')}/{r.get('control_seconds')} {r.get('error') or ''}", flush=True)
        return r

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        reports = list(pool.map(launch, runs))
    diverging = [r for r in reports if r['verdict'] == 'diverges']
    full = sum(r.get('control_seconds') or 0 for r in reports)
    summary = {'tag': args.tag, 'completed': now(), 'treatment': treatment, 'host_keys': sorted(host_keys),
               'planner_sha256': planner_sha(code), 'runs': len(reports),
               'exempt': sorted(Path(r['run']).name for r in reports if r['verdict'] == 'exempt'),
               'diverges': len(diverging), 'errors': [r['run'] for r in reports if r['verdict'] == 'error'],
               'rule_violations': [r['run'] for r in reports if r.get('rule_violation')],
               # Real seconds the treatment arm still needs, at recorded speed:
               # whole runs (recorded control, no fork) and forks from the divergence (R2b).
               'control_seconds_total': round(full, 1),
               'treatment_seconds_without_fork': round(sum(r.get('control_seconds') or 0 for r in diverging), 1),
               'treatment_seconds_with_fork': round(sum((r.get('control_seconds') or 0) - r['divergence']['fork_seconds']
                                                        for r in diverging), 1),
               'note': 'treatment seconds assume the treatment takes as long as the control after the fork; '
                       'they size the work, they are not results'}
    write(out / 'summary.json', summary)
    print(json.dumps({k: v for k, v in summary.items() if k != 'exempt'} | {'exempt': len(summary['exempt'])}, ensure_ascii=False))
    raise SystemExit(1 if summary['errors'] else 0)


if __name__ == '__main__':
    main()
