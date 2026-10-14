"""G3 routing sensitivity (execution thread's design, g3 plan section 6 (b)): does a checkpoint restore lose state
for the tier-A types on the scratch-route list (g2/unsaved-state.json route_to_scratch)?

For the main G3 verdict the 17-type list is fixed; this check only feeds the post-hoc sensitivity analysis.

Doors: g3/sample.json doors not in G3 look 1 (look1 false, not skipped), so no overlap with first-look attempts.
Per routed type at least --per-type doors (all there are when fewer), seeds round-robin, order sha256('route:' type
door); BingBong (known unfaithful, R2b) is the positive control. Pre-selection reads the F1-door observation
(relics, deck ids); the label of record is fork_check.needs_scratch() on the door checkpoint the setup writes.

Per door, no solving (recorded actions only):
  setup      game start -> F1 door (scratch, map checkpoints on, state dump)
  door-ckpt  last map checkpoint <= F1 door -> F1 door (state dump)          compared with setup
  f2-scratch game start -> F2 entry via the recorded line (fresh process)
  f2-ckpt    checkpoint -> F2 entry                                          compared with f2-scratch
Comparison is fork_check.compare_dumps (lazy caches, unset star cost and map RNG explained; forced block exact).
A diff is attributed to a type when its path names that class ('/BingBong._cardsToSkip').

  python tools/in_job.py --job-mib 24576 tools/route_check.py --out DIR [--per-type 12] [--workers 6] [--dry-run]
"""
from pathlib import Path
from collections import defaultdict
import argparse
import ctypes
import hashlib
import json
import os
import re
import time

import fork_check as fc
import f1_member_check as i1

SCHEMA = 'spire-route-check/v1'
E_CORES = 0xFFFF0000


def observed_ids(observation):
    ids = {'RELIC.' + r for r in observation.get('relics') or ()}
    ids |= {'CARD.' + c['id'] for c in observation.get('deck') or ()}
    return ids


def select(per_type, controls):
    routes = fc.scratch_routes()
    doors = [r for r in json.loads((i1.EXPERIMENTS / 'g3/sample.json').read_text(encoding='utf-8'))['doors']
             if not r['look1'] and not r['skipped']]
    by_type = defaultdict(list)
    info = {}
    for r in doors:
        ev = i1.EXPERIMENTS / 'iteration-105-ablation-runs' / r['run'] / f"seed-{r['seed']}" / r['eval']
        observation = i1.decision_of(ev)['decision_evidence'][r['f1_index']]['observation']
        types = sorted({routes[i] for i in observed_ids(observation) if i in routes})
        key = f"{r['run']}--{r['eval']}"
        info[key] = dict(r, ev=str(ev), observed_types=types)
        for t in types:
            by_type[t].append(key)
    chosen = {}
    for t, keys in sorted(by_type.items()):
        want = controls if t == 'BingBong' else per_type
        keys = sorted(keys, key=lambda k: hashlib.sha256(f'route:{t}:{info[k]["door"]}'.encode()).hexdigest())
        seeds = defaultdict(list)
        for k in keys:
            seeds[info[k]['seed']].append(k)
        picked = []
        while len(picked) < want and any(seeds.values()):
            for s in sorted(seeds, key=lambda s: hashlib.sha256(f'route:{t}:{s}'.encode()).hexdigest()):
                if seeds[s] and len(picked) < want:
                    picked.append(seeds[s].pop(0))
        for k in picked:
            chosen.setdefault(k, set()).add(t)
    return {k: dict(info[k], selected_for=sorted(v)) for k, v in sorted(chosen.items())}, \
        {t: len(v) for t, v in by_type.items()}


def attribute(diffs, names):
    out = defaultdict(list)
    for d in diffs:
        path = d.get('path', '') if isinstance(d, dict) else str(d)
        hit = [n for n in names if re.search(rf'(^|/){n}\.', path)]
        for n in hit or ['(other)']:
            out[n].append(path)
    return dict(out)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--per-type', type=int, default=12)
    p.add_argument('--controls', type=int, default=3)
    p.add_argument('--workers', type=int, default=6)
    p.add_argument('--runtime-profile', default='server-large-gen0')
    p.add_argument('--timeout', type=float, default=1800)
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    doors, available = select(a.per_type, a.controls)
    per = defaultdict(int)
    for d in doors.values():
        for t in d['selected_for']:
            per[t] += 1
    print(f'{len(doors)} doors; selected per type {dict(per)}; available {available}', flush=True)
    if a.dry_run:
        return
    if a.out.exists():
        p.error('--out must be new')
    if os.name == 'nt':
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.GetCurrentProcess.restype = ctypes.c_void_p
        k.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        if not k.SetProcessAffinityMask(k.GetCurrentProcess(), E_CORES):
            raise ctypes.WinError(ctypes.get_last_error())
        os.environ['SPIRE_TARGET_CPUS'] = str(bin(E_CORES).count('1'))
    sdk = i1.ROOT.parents[2] / '.tools/dotnet'
    if (sdk / 'dotnet.exe').is_file():
        os.environ['PATH'] = str(sdk) + os.pathsep + os.environ.get('PATH', '')
        os.environ['DOTNET_ROOT'] = str(sdk)
    from spire_exact.native import game_data
    from spire_exact.planning.pool import NativePool, WorkerError
    from spire_exact.planning.resources import ResourcePlan, enclosing_job_memory_limit
    limit = enclosing_job_memory_limit()
    if not limit:
        p.error('run inside tools/in_job.py')
    resources = ResourcePlan.detect(a.workers, 1, None, 1024, limit, shared=True)
    data = game_data(i1.ROOT / 'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64')
    from spire_exact.game_profile import require_routing
    from spire_exact.upstream import file_hash
    profile = require_routing(file_hash(data / 'sts2.dll'))  # routing table belongs to this game build
    a.out.mkdir(parents=True)
    R = a.out / 'requests'
    report = {'schema': SCHEMA, 'started': time.strftime('%Y-%m-%dT%H:%M:%S'), 'affinity': hex(E_CORES),
              'runtime_profile': a.runtime_profile, 'per_type': a.per_type, 'available_doors_per_type': available,
              'routing_sha256': hashlib.sha256(fc.UNSAVED_STATE.read_bytes()).hexdigest(),
              'game_sha256': profile['game']['game_sha256'],
              'harness_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'doors': []}
    errors = []

    def run_all(pool, jobs):
        futures = [(tag, pool.submit(req, R / tag, a.timeout, fresh=fresh)) for tag, req, fresh in jobs]
        out = {}
        for tag, future in futures:
            try:
                out[tag] = future.result()[0]
            except (WorkerError, OSError, ValueError, KeyError) as exc:
                errors.append((tag, repr(exc))); out[tag] = None
            print(tag, 'error' if out[tag] is None else out[tag].get('status'), flush=True)
        return out

    def dump_of(tag):
        f = R / tag / 'data/state-dump.json'
        return json.loads(f.read_text(encoding='utf-8')) if f.exists() else None

    with NativePool(data, a.out / 'workers', resources, runtime_profile=a.runtime_profile,
                    memory_policy='shared') as pool:
        caps = set(pool.stamp.get('capabilities') or [])
        if 'replay.state_dump' not in caps:
            raise SystemExit('host lacks replay.state_dump')
        report['host_sha256'] = pool.stamp['host_sha256']
        loaded = {}
        wave = []
        for key, d in doors.items():
            ev = Path(d['ev'])
            request, decision = i1.read(ev / 'request.json'), i1.decision_of(ev)
            f2, _after = fc.door_lines(decision, d['f1_index'])
            loaded[key] = (request, decision['trace'], f2['index'])
            setup = fc.base(request); setup['history'] = decision['trace'][:d['f1_index']]
            setup['capture_checkpoints'] = True; setup['dump_state_at_end'] = True; setup['capture_rng_streams'] = True
            wave.append((key + '--setup', setup, True))
            wave.append((key + '--f2-scratch', fc.fork_request(request, decision['trace'][:f2['index']]), True))
        got = run_all(pool, wave)
        wave = []
        for key, d in doors.items():
            request, trace, f2_index = loaded[key]
            cps = sorted((R / (key + '--setup') / 'data/checkpoints').glob('map-*.json*'))
            cps = [c for c in cps if int(c.name.split('-')[1].split('.')[0]) <= d['f1_index']]
            d['checkpoint'] = str(cps[-1]) if cps else None
            if not d['checkpoint']:
                continue
            door = fc.fork_request(request, trace[:d['f1_index']]); door['checkpoint'] = d['checkpoint']
            wave.append((key + '--door-ckpt', door, False))
            f2 = fc.fork_request(request, trace[:f2_index]); f2['checkpoint'] = d['checkpoint']
            wave.append((key + '--f2-ckpt', f2, False))
        got.update(run_all(pool, wave))

    names = sorted(fc.scratch_routes(conservative=True).values())
    for key, d in doors.items():
        row = {k: d.get(k) for k in ('seed', 'run', 'eval', 'door', 'f1_index', 'observed_types', 'selected_for',
                                     'checkpoint')}
        row['routed_types'] = fc.needs_scratch(d['checkpoint']) if d.get('checkpoint') else None
        row['routed_types_conservative'] = fc.needs_scratch(d['checkpoint'], True) if d.get('checkpoint') else None
        for point, scratch, ckpt in (('at_door', '--setup', '--door-ckpt'), ('at_f2', '--f2-scratch', '--f2-ckpt')):
            sc, ck = dump_of(key + scratch), dump_of(key + ckpt)
            if sc is None or ck is None:
                row[point] = {'error': 'missing dump'}; continue
            c = fc.compare_dumps(sc, ck, fc.last_decision_row(R / (key + scratch))[0],
                                 fc.last_decision_row(R / (key + ckpt))[0])
            c['by_type'] = attribute(c['value_diffs'] + c['forced_diffs'] + c['id_diffs'] + c['sidecar_diffs'], names)
            row[point] = c
        report['doors'].append(row)

    summary = {}
    for t in sorted({t for d in doors.values() for t in d['selected_for']}):
        rows = [r for r in report['doors'] if t in (r['routed_types'] or r['selected_for'])]
        ok = [r for r in rows if 'error' not in r['at_door'] and 'error' not in r['at_f2']]
        bad = [r for r in ok if t in r['at_door']['by_type'] or t in r['at_f2']['by_type']]
        unequal = [r for r in ok if not (r['at_door']['equal'] and r['at_f2']['equal'])]
        n = len(ok)
        # Clopper-Pearson one-sided 95 % upper bound for 0 failures: 1 - 0.05 ** (1 / n)
        summary[t] = {'doors': n, 'seeds': len({r['seed'] for r in ok}), 'unfaithful': len(bad), 'any_unequal': len(unequal),
                      'upper95_if_zero': round(1 - 0.05 ** (1 / n), 3) if n and not bad else None,
                      'unfaithful_doors': [r['door'] for r in bad]}
    report['summary'] = summary
    report['other_diffs'] = sum(1 for r in report['doors'] for pt in ('at_door', 'at_f2')
                                if '(other)' in (r.get(pt) or {}).get('by_type', {}))
    report['errors'] = errors
    report['finished'] = time.strftime('%Y-%m-%dT%H:%M:%S')
    (a.out / 'route-check.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
