"""Machine-speed calibration for recorded controls (PLAN-v2 R2a point 3).

A recorded control is compared with a treatment that runs later, possibly on
a busier or cooler machine. The work itself is deterministic, so re-running
the same recorded requests measures the machine and nothing else.

This tool re-submits fixed requests of one reference recording to a real
NativePool (requests that start from the game start: no checkpoint, prefix
length 0, so they run standalone), byte for byte as recorded apart from the
output folder, with the recording's worker count and placement, and records:

- per request: wall seconds, and whether the result equals the recording
  (classification, actions, expanded combat nodes) - a determinism check;
- the speed factor against a baseline calibration of the same tool
  (--baseline): the median of per-request wall ratios, and the makespan ratio.
  A factor above 1 + --threshold (pre-registered, default 5 %) is flagged.

Run it at the start and at the end of every experiment session; the pair
brackets the session. It starts the game host on the P cores the reference
used, so it is a real job: never while another job runs.

  python tools/speed_calibration.py --reference RUN_DIR [--reference RUN_DIR ...] --out DIR [--count 14] [--baseline DIR/calibration.json]

A recording holds only a few standalone requests (the baseline and the root
policies), so --reference may be repeated: the requests are taken from the
references in the order given. All references must have the same host,
worker count and planner settings; the first one gives the CPU placement.
"""
from pathlib import Path
import argparse
import ctypes
import hashlib
import json
import os
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from replay_golden import FAILED, ROOT, canonical, decision as recorded_decision, now, read, recording, rows, write  # noqa: E402

SCHEMA = 'spire-speed-calibration/v1'


def record_only(value):
    """Drop observation fields a host writes for people only. `selection.cards`
    (the candidates of a select_cards screen, added for SpireBoard) is read by
    no planner code; recordings from hosts without it are unchanged by this."""
    if isinstance(value, dict):
        out = {k: record_only(v) for k, v in value.items()}
        if isinstance(out.get('selection'), dict):
            out['selection'] = {k: v for k, v in out['selection'].items() if k != 'cards'}
        return out
    if isinstance(value, list):
        return [record_only(v) for v in value]
    return value


def projection(decision):
    """The deterministic part of a worker decision: trace, end state, value
    and expanded nodes (advisor_metrics and performance also hold timings)."""
    searches = (decision.get('advisor_metrics') or {}).get('searches') or []
    return {'trace_sha256': hashlib.sha256(canonical(record_only(decision.get('trace')))).hexdigest(),
            'observation_sha256': hashlib.sha256(canonical(record_only(decision.get('observation')))).hexdigest(),
            'status': decision.get('status'), 'value': decision.get('value'),
            'expanded_nodes': sum(int(s.get('expanded_nodes') or 0) for s in searches)}


def settings_of(manifest):
    """Recorded planner argv -> {option: value or True}."""
    out, argv = {}, list(manifest.get('settings') or [])
    for i, item in enumerate(argv):
        if item.startswith('--'):
            nxt = argv[i + 1] if i + 1 < len(argv) else None
            out[item[2:]] = nxt if nxt is not None and not nxt.startswith('--') else True
    return out


def recorded_cpus(run, seed):
    for row in rows(run / f'seed-{seed}-events.jsonl'):
        if row.get('event') == 'started':
            return row.get('cpus')
    return None


def same(settings):
    """Planner settings apart from the seed, which differs between references by design."""
    return {k: v for k, v in settings.items() if k != 'seed'}


def standalone(seed_dir, ledger, count):
    """First `count` recorded requests that run from the game start."""
    picked = []
    for row in ledger:
        if row.get('cache_hit') or row.get('classification') in FAILED or row.get('prefix_length') or row.get('checkpoint_prefix'):
            continue
        request = read(seed_dir / row['label'] / 'request.json')
        if request.get('checkpoint') or request.get('history'):
            continue
        picked.append((row, request))
        if len(picked) == count:
            break
    return picked


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--reference', type=Path, action='append', required=True, help='recorded run directory (repeatable)')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--count', type=int, default=14, help='requests (default: two per worker of a 7-worker recording)')
    parser.add_argument('--baseline', type=Path, help='an earlier calibration.json of the same reference')
    parser.add_argument('--threshold', type=float, default=0.05)
    parser.add_argument('--worker-memory-policy', help='default: the recording\'s')
    parser.add_argument('--worker-mib', type=int, help='per-worker memory when the policy is not shared')
    parser.add_argument('--timeout', type=float, default=1200)
    parser.add_argument('--other-host', action='store_true',
                        help='host-equivalence check: run on a host other than that of the recording and require '
                             'identical decisions (the report then also serves as the baseline of that host)')
    args = parser.parse_args()
    if args.out.exists() and any(args.out.iterdir()):
        parser.error('--out must be new')
    picked, refs, settings, cpus, host, original = [], [], None, None, None, None
    for ref in args.reference:
        manifest, seed_dir, rec, ledger, _ = recording(ref)
        identity = read(seed_dir / ledger[0]['label'] / 'data/identity.json')
        if settings is None:
            settings, original, host = settings_of(manifest), rec, identity['host_sha256']
            cpus = recorded_cpus(ref, manifest['seed'])
        elif same(settings_of(manifest)) != same(settings) or identity['host_sha256'] != host:
            parser.error(f'{ref.name}: other planner settings or host than {args.reference[0].name}')
        refs.append({'run': str(ref), 'seed': manifest['seed']})
        for row, request in standalone(seed_dir, ledger, args.count - len(picked)):
            picked.append((ref.name + '/' + row['label'], seed_dir, row, request))
        if len(picked) == args.count:
            break
    if len(picked) < args.count:
        parser.error(f'the references hold only {len(picked)} standalone requests')
    if os.name == 'nt' and cpus:
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.GetCurrentProcess.restype = ctypes.c_void_p
        k.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        if not k.SetProcessAffinityMask(k.GetCurrentProcess(), sum(1 << c for c in cpus)):
            raise ctypes.WinError(ctypes.get_last_error())
        os.environ['SPIRE_TARGET_CPUS'] = str(len(cpus))
    sys.path.insert(0, str(ROOT))
    sdk = ROOT.parents[2] / '.tools/dotnet'
    if (sdk / 'dotnet.exe').is_file():
        os.environ['PATH'] = str(sdk) + os.pathsep + os.environ.get('PATH', '')
        os.environ['DOTNET_ROOT'] = str(sdk)
    from spire_exact.native import game_data
    from spire_exact.planning.pool import NativePool, WorkerError
    from spire_exact.planning.resources import ResourcePlan, enclosing_job_memory_limit
    workers = int(settings.get('workers', original['resources']['workers']))
    policy = args.worker_memory_policy or settings.get('worker-memory-policy', 'hard')
    if policy == 'shared':
        limit = enclosing_job_memory_limit()
        if not limit:
            parser.error('shared memory policy needs an enclosing job memory limit (launch inside a job, or pass '
                         '--worker-memory-policy hard --worker-mib N)')
        resources = ResourcePlan.detect(workers, 1, None, int(settings.get('reserve-mib', 1024)), limit, shared=True)
    else:
        resources = ResourcePlan.detect(workers, 1, args.worker_mib or int(settings.get('worker-memory-mib', 1792)),
                                        int(settings.get('reserve-mib', 1024)))
    if resources.workers != workers:
        raise SystemExit('the worker count cannot fit; refuse to calibrate with a clamped pool')
    data = game_data(ROOT / 'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64')
    args.out.mkdir(parents=True, exist_ok=True)
    report = {'schema': SCHEMA, 'started': now(), 'references': refs,
              'labels': [key for key, *_ in picked], 'workers': workers, 'cpus': cpus,
              'memory_policy': policy, 'resources': resources.as_dict(),
              'harness_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    started = time.perf_counter()
    results = []
    with NativePool(data, args.out / 'workers', resources, runtime_profile=settings.get('runtime-profile', 'legacy'),
                    memory_policy=policy) as pool:
        if pool.stamp['host_sha256'] != host and not args.other_host:
            raise SystemExit(f"host {pool.stamp['host_sha256'][:12]} is not the reference's host "
                             f"{host[:12]}: a different host is not a speed calibration")
        report['host_sha256'] = pool.stamp['host_sha256']
        report['reference_host_sha256'] = host
        report['capabilities'] = pool.stamp.get('capabilities')
        futures = [(key, seed_dir, row, pool.submit(request, args.out / key.replace('/', '--'), args.timeout))
                   for key, seed_dir, row, request in picked]
        for key, seed_dir, row, future in futures:
            entry = {'label': key, 'recorded_job_wall_seconds': (row.get('runtime') or {}).get('job_wall_seconds')}
            try:
                decision, _ = future.result()
                transport = read(args.out / key.replace('/', '--') / 'transport.json')
                entry['wall_seconds'] = transport.get('wall_seconds')
                got, want = projection(decision), projection(recorded_decision(seed_dir, row['label']))
                entry['identical'] = got == want
                if not entry['identical']:
                    entry['differs'] = sorted(k for k in got if got[k] != want[k])
            except (WorkerError, OSError, ValueError, KeyError) as exc:
                entry.update(error=repr(exc), identical=False)
            results.append(entry)
    report['makespan_seconds'] = round(time.perf_counter() - started, 3)
    report['requests'] = results
    report['all_identical'] = all(r.get('identical') for r in results)
    if report['host_sha256'] != host:
        report['host_equivalence'] = {'reference_host_sha256': host, 'host_sha256': report['host_sha256'],
                                      'requests': len(results), 'equivalent': report['all_identical']}
    if args.baseline:
        base = read(args.baseline)
        if base.get('labels') != report['labels'] or base.get('host_sha256') != report['host_sha256']:
            raise SystemExit('baseline used other requests or another host')
        by = {r['label']: r for r in base['requests']}
        ratios = [r['wall_seconds'] / by[r['label']]['wall_seconds'] for r in results
                  if r.get('wall_seconds') and by.get(r['label'], {}).get('wall_seconds')]
        factor = statistics.median(ratios) if ratios else None
        report['against_baseline'] = {
            'baseline': str(args.baseline), 'median_wall_ratio': factor,
            'makespan_ratio': report['makespan_seconds'] / base['makespan_seconds'],
            'threshold': args.threshold,
            'drift': None if factor is None else abs(factor - 1) > args.threshold}
    report['completed'] = now()
    write(args.out / 'calibration.json', report)
    print(json.dumps({k: report.get(k) for k in ('makespan_seconds', 'all_identical', 'host_equivalence', 'against_baseline')}, ensure_ascii=False))
    raise SystemExit(0 if report['all_identical'] else 1)


if __name__ == '__main__':
    main()
