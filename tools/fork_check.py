"""G2 fork tool and its pre-registered fidelity checks (g2/plan.md section 5).

A fork replays the real prefix to an F1 door, plays a chosen F1 line, enters
F2 natively by the original path (the source trajectory's reward and map
actions between F1 and F2), and stops at the first F2 decision with a full
state dump (host `dump_state_at_end`, StateDump.cs: Player / run save, every
pile in order, creatures, powers, relics, monster and encounter RNG fields,
CombatState) plus the G1 RNG sidecar (`capture_rng_streams`). `fork_solve` is
the same history without the dump: the host solves F2 with the recorded
advisor and stops after F2 (stop_after_combat).

Per door (one new-host 'setup' replay from the game start to the F1 door
writes the map checkpoint before F1; 105 checkpoints belong to another host
and are not loaded across hosts) the lines are the recorded F1 line
(default) and up to --alternatives P1 alternatives (one-member FinalBoss1
re-solve of F1, as tools/f1_member_check.py; the alternative then follows the
source's own reward / map actions into F2).

Checks (all must hold for 100 % of the sample):
  (1) default fork = record: F2 entry observation equals the recorded evidence;
      the default F2 re-solve reproduces the recorded F2 forecasts and nodes.
  (2) fork in a reused worker process = fork in a fresh process (dump + sidecar).
  (3) the same fork twice (fresh processes): state-dump.json byte-identical.
  (5)=(6) checkpoint-restored fork = fork replayed from the game start, every
      dump field (instance ids reported in a separate column) and the sidecar.
  hazards: TestRngInjector overrides null, ModifyShuffleOrder users recorded,
      the dump moved no RNG (dump_side_effect_free), no reseed in the sidecar.
Check (4) (all new request switches off = frozen-i054a-shm byte for byte)
is the snapshot hand-off check, run separately.

Timings (G3 main cost = restore + F1 and F2 entry + F2 solve):
  setup     setup request wall (once per door)
  restore   checkpoint fork, stage run_setup_or_restore
  f1_entry  checkpoint fork wall - restore
  f2_solve  checkpoint fork_solve wall - checkpoint fork wall

  python tools/in_job.py --job-mib 24576 tools/fork_check.py --out DIR [--doors 20] [--alternatives 2] [--dry-run]
"""
from pathlib import Path
import argparse
import ctypes
import gzip
import hashlib
import json
import os
import re
import sys
import time
from copy import deepcopy

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import f1_member_check as i1  # noqa: E402
from spire_exact.planning.gatemodel import boss_fight_rows  # noqa: E402
from spire_exact.planning.plugins.reroll import boss_search_groups, first_call, last_act_boss_plan  # noqa: E402
from speed_calibration import record_only  # noqa: E402

SCHEMA = 'spire-fork-check/v1'
ID_WORDS = ('CombatId', '_nextCreatureId', 'NetId', 'InstanceId', '_id')
# Differences that are not carried state, each checked in the game source
# (g1-rng/decompiled, v0.111.0). Everything else counts against the check.
EXPLAINED = {
    # getters fill these on first use: `_x ?? (_x = ...)` (CardModel.cs:108, 302-314, 534)
    # (CardModel.cs:108, 302-314, 534-548; PotionModel.cs:134-144). Accepted only when
    # the dump's `forced` block, taken after running these getters on both sides, is equal.
    'lazy_cache': ('CardModel._tags', 'CardModel._pool', 'CardModel._titleLocString', 'CardModel._dynamicVars',
                   'PotionModel._dynamicVars'),
    # BaseStarCost reads _baseStarCost only after setting it when _starCostSet is false
    # (CardModel.cs:412-433); the stored value is dead until then. Also checked by `forced`.
    'unset_star_cost': ('CardModel._baseStarCost',),
    # used only while StandardActMap generates the map (StandardActMap.cs:92-353);
    # a map restored from the save (SavedActMap) has no such stream
    'map_generation_rng': ('StandardActMap._rng',),
    # relic glow state, not saved; read by the shader (RelicModel.cs:487-503, NRelicInventoryHolder)
    # and in game code only by PaelsFlesh's ShowCounter and its Flash guard (PaelsFlesh.cs:27, 76);
    # behaviour uses saved fields (LavaRock.HasTriggered, LavaRock.cs:52-62). route-check-20261013c
    'relic_display_status': ('RelicModel._status',),
    # profile progress lists copied into the player (Player.cs:271-275); read by
    # UI / statistics screens and merged at run end only; a reused process accumulates them
    'profile_discovery': ('discovered_cards', 'discovered_enemies', 'discovered_epochs', 'discovered_potions',
                          'discovered_relics'),
}


LAZY = ('lazy_cache', 'unset_star_cost')


def explain(path, a, b, side_a, side_b, lazy=True):
    leaf = path.rsplit('/', 1)[-1].split('[', 1)[0]   # discovered_epochs[2] -> discovered_epochs
    for kind, names in EXPLAINED.items():
        if leaf not in names or (kind in LAZY and not lazy):
            continue
        if kind == 'lazy_cache' and a is not None and b is not None:
            continue
        if kind == 'unset_star_cost':
            flag = path.rsplit('/', 1)[0] + '/CardModel._starCostSet'
            if lookup(side_a, flag) is not False or lookup(side_b, flag) is not False:
                continue
        return kind
    return None


def lookup(value, path):
    for part in path.strip('/').split('/'):
        if value is None:
            return None
        name, *idx = part.split('[')
        value = value.get(name) if isinstance(value, dict) else None
        for i in idx:
            value = value[int(i[:-1])] if isinstance(value, list) and int(i[:-1]) < len(value) else None
    return value


def compare(a, b, lazy=True):
    diffs, ids = split_ids(a, b)
    explained, other = {}, []
    for path in diffs:
        kind = explain(path, lookup(a, path), lookup(b, path), a, b, lazy)
        if kind:
            explained[kind] = explained.get(kind, 0) + 1
        else:
            other.append(path)
    return other, ids, explained


def in_combat(row):
    obs = row.get('observation') or {}
    return row.get('phase') == 'combat' or (row.get('phase') == 'select_cards' and obs.get('turn') is not None)


def door_lines(decision, f1_index):
    """(F2 entry index, first index after F1) of the source trajectory."""
    rows = boss_fight_rows(decision)
    f1 = next(r for r in rows if r['index'] == f1_index)
    f2 = next(r for r in rows if r['gate'] == (f1['gate'][0], 1))
    ev = decision['decision_evidence']
    after = next(i for i in range(f1_index, f2['index']) if not in_combat(ev[i]))
    return f2, after


def base(request):
    out = deepcopy(request)
    for key in ('checkpoint', 'out', 'expected_evidence'):
        out.pop(key, None)
    return out


def fork_request(request, history, dump=True):
    out = base(request)
    out['history'] = history
    out['capture_rng_streams'] = True
    if dump:
        out['dump_state_at_end'] = True
    else:
        out['stop_after_combat'] = True
    return out


def split_ids(a, b, path=''):
    """Differences between two JSON values: (value paths, instance-id-only paths)."""
    diffs, ids = [], []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            d, i = split_ids(a.get(k), b.get(k), path + '/' + k)
            diffs += d; ids += i
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for n, (x, y) in enumerate(zip(a, b)):
            d, i = split_ids(x, y, f'{path}[{n}]')
            diffs += d; ids += i
    elif a != b:
        (ids if any(w in path.rsplit('/', 1)[-1] for w in ID_WORDS) else diffs).append(path)
    return diffs, ids


def last_decision_row(directory):
    path = directory / 'data/rng-streams.jsonl'
    rows = [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()]
    row = [r for r in rows if r['point'] == 'decision'][-1]
    return {k: v for k, v in row.items() if k != 'sequence'}, rows


def streams(value, path=''):
    """Every RNG state {counter, s0..s3} in a sidecar row, by path."""
    if isinstance(value, dict):
        if 'counter' in value and 's0' in value:
            yield path, value
        else:
            for k, v in value.items():
                yield from streams(v, path + '/' + str(k))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from streams(v, f'{path}[{i}]')


def reseeds(rows):
    """(stream count, streams whose counter went backwards or kept its counter with
    another state between consecutive sidecar rows). Room-scoped local streams
    are recreated per room, so only run and player streams are checked."""
    bad, last, seen = [], {}, set()
    for row in rows:
        for path, v in streams({'run_rng': row.get('run_rng'), 'players': row.get('players')}):
            seen.add(path)
            prev = last.get(path)
            if prev and (v['counter'] < prev['counter'] or (v['counter'] == prev['counter'] and v != prev)):
                bad.append((row['sequence'], path))
            last[path] = v
    return {'streams': len(seen), 'bad': bad}


def wall(directory):
    perf = json.loads((directory / 'data/performance.json').read_text(encoding='utf-8'))
    return perf['wall_us'] / 1e6, perf.get('exclusive_stages') or {}


def compare_dumps(scratch, other, scratch_row, other_row):
    """One fork against the scratch replay: state dump, `forced` block and RNG sidecar row."""
    strip = lambda x: {k: v for k, v in x.items() if k != 'forced'}
    diffs, ids, explained = compare(strip(scratch), strip(other))
    # condition (a): after forcing the lazy getters on both sides, no lazy exemption applies
    fdiffs, fids, _ = compare(scratch.get('forced'), other.get('forced'), lazy=False)
    side, side_ids, side_explained = compare(scratch_row, other_row)
    forced_equal = 'forced' in scratch and 'forced' in other and not fdiffs and not fids
    return {'equal': not diffs and not ids and forced_equal, 'value_diffs': diffs[:20],
            'n_value_diffs': len(diffs), 'id_diffs': ids[:20], 'n_id_diffs': len(ids),
            'forced_equal': forced_equal, 'forced_diffs': (fdiffs + fids)[:20],
            'profile_discovery': explained.get('profile_discovery', 0), 'explained': explained,
            'sidecar_equal': not side and not side_ids, 'sidecar_diffs': (side + side_ids)[:20],
            'sidecar_explained': side_explained}


UNSAVED_STATE = HERE.parent / 'configs' / 'unsaved-state.json'  # copy of g2/unsaved-state.json (tools/unsaved_state_scan.py)
MODEL_ID = re.compile(r'(RELIC|CARD|POTION|POWER|ENCHANTMENT|AFFLICTION|MODIFIER)\.[A-Z0-9_]+')


def model_id(type_name, kind):
    """Game model id of a model class: BingBong/relic -> RELIC.BING_BONG (checked on 291 class:id pairs in 689db550 dumps)."""
    snake = re.sub(r'(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])', '_', type_name).upper()
    return f'{kind.upper()}.{snake}'


def scratch_routes(conservative=False, path=UNSAVED_STATE):
    """Model id -> class name for the G3 safeguard-1 list (route_to_scratch, or the 47-type conservative list)."""
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    kinds = {e['type']: e['kind'] for e in data['entries']}
    names = data['route_to_scratch_conservative' if conservative else 'route_to_scratch']
    return {model_id(n, kinds[n]): n for n in names}


def owned_model_ids(save):
    """Model ids the run owns in a run save (map checkpoint payload): every id outside the map history,
    discovered lists and relic grab bags (players' relics, deck cards and their enchantments, potions, modifiers)."""
    found = set()

    def walk(o, key=''):
        if key == 'map_point_history' or key.startswith('discovered') or 'grab_bag' in key:
            return
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, k)
        elif isinstance(o, list):
            for v in o:
                walk(v, key)
        elif isinstance(o, str) and MODEL_ID.fullmatch(o):
            found.add(o)
    walk(save)
    return found


def needs_scratch(checkpoint, conservative=False, routes=None):
    """G3 safeguard 1: sorted class names on the route list the door's checkpoint save owns ([] = checkpoint path).

    `checkpoint` is a map checkpoint file (.json.gz / .json), its loaded wrapper {schema, sha256, payload, ...},
    or the bare run save. Only objects alive at the restore point matter: anything gained after the door is built
    live by the replay, so its unsaved fields are exact."""
    if isinstance(checkpoint, (str, Path)):
        p = Path(checkpoint)
        raw = gzip.open(p, 'rt', encoding='utf-8').read() if p.suffix == '.gz' else p.read_text(encoding='utf-8')
        checkpoint = json.loads(raw)
    save = checkpoint.get('payload', checkpoint) if isinstance(checkpoint, dict) else checkpoint
    if isinstance(save, str):
        save = json.loads(save)
    routes = routes if routes is not None else scratch_routes(conservative)
    return sorted({routes[i] for i in owned_model_ids(save) if i in routes})


def spot_check(key, rate=0.10, salt='g3-scratch-spot'):
    """G3 safeguard 2 sampling: a fixed-seed draw, True for about `rate` of keys (sha256(salt:key))."""
    return int(hashlib.sha256(f'{salt}:{key}'.encode()).hexdigest()[:8], 16) < rate * 2 ** 32


def verify_against_scratch(pool, request, out_dir, timeout=1800):
    """G3 safeguard 2: check one checkpoint-path request against a scratch replay.

    `request` is the attempt as sent (history + checkpoint; stop_after_combat or not). The same
    history runs twice more with dump_state_at_end and the RNG sidecar: from the checkpoint on the
    pool, and from game start in a fresh process. Both stop at the first decision after the history
    (the F2 entry for a G3 attempt) and are compared under the fork-check rules: full carry state,
    `forced` lazy caches without exemption, and the RNG sidecar. `equal` False means stop the run,
    extend g2/unsaved-state.json and recompute the affected doors (g2/plan.md section 6).
    """
    if not request.get('checkpoint'):
        raise ValueError('request has no checkpoint')
    out_dir = Path(out_dir)
    ck = fork_request(request, request['history']); ck['checkpoint'] = request['checkpoint']
    sc = fork_request(request, request['history'])
    futures = {'ckpt': pool.submit(ck, out_dir / 'ckpt', timeout, fresh=False),
               'scratch': pool.submit(sc, out_dir / 'scratch', timeout, fresh=True)}
    status = {k: f.result()[0].get('status') for k, f in futures.items()}
    dumps = {}
    for k in futures:
        path = out_dir / k / 'data/state-dump.json'
        dumps[k] = json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
    if dumps['scratch'] is None or dumps['ckpt'] is None:
        return {'equal': False, 'error': 'no dump', 'status': status}
    result = compare_dumps(dumps['scratch'], dumps['ckpt'], last_decision_row(out_dir / 'scratch')[0],
                           last_decision_row(out_dir / 'ckpt')[0])
    result['equal'] = result['equal'] and result['sidecar_equal']
    result['status'] = status
    result['scratch_wall'] = wall(out_dir / 'scratch')[0]
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--doors', type=int, default=20)
    parser.add_argument('--alternatives', type=int, default=2)
    parser.add_argument('--repeats', type=int, default=10)
    parser.add_argument('--workers', type=int, default=7)
    # iteration-105 workers ran server-large-gen0 (2 CLR processors, server GC, 1 GiB gen0)
    parser.add_argument('--runtime-profile', default='server-large-gen0')
    parser.add_argument('--timeout', type=float, default=1800)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    picked = i1.sample(i1.read(i1.EXPERIMENTS / 'g2/door-inventory.json'), i1.EXPERIMENTS / 'iteration-105-ablation-runs',
                       args.alternatives)
    # Fixed stratified sample: one door per seed (f1_member_check's rule), seeds by sha256('g2:' + seed).
    picked = sorted(picked, key=lambda p: hashlib.sha256(('g2:' + p[1].split('-')[2]).encode()).hexdigest())[:args.doors]
    doors = []
    for door, name, ev, request, decision, f1, deployed, alternatives, call in picked:
        f2, after = door_lines(decision, f1['index'])
        groups = boss_search_groups(decision)
        played = [r['index'] for r in boss_fight_rows(decision) if r['index'] >= len(request['history'])]
        f2_call = first_call(groups[played.index(f2['index'])])
        plan = last_act_boss_plan(request['advisor'])
        doors.append({'tag': f"{name}--{door['eval']}", 'run': name, 'eval': door['eval'], 'door': door['key'],
                      'request': request, 'trace': decision['trace'], 'f1_index': f1['index'], 'after': after,
                      'f2_index': f2['index'], 'f2_entry': decision['decision_evidence'][f2['index']]['observation'],
                      'f2_recorded': [{k: (r.get('forecast') or {}).get(k) for k in i1.FORECAST} for r in f2_call],
                      'f2_recorded_nodes': [r.get('expanded_nodes') for r in f2_call],
                      'alternatives': [(int(a['member_index']), plan['members'][int(a['member_index'])]) for a in alternatives]})
    print(f"{len(doors)} doors, {sum(len(d['alternatives']) for d in doors)} alternatives", flush=True)
    if args.dry_run:
        for d in doors:
            print(d['tag'], d['f1_index'], d['after'], d['f2_index'], [j for j, _ in d['alternatives']])
        return
    if args.out.exists() and any(args.out.iterdir()):
        parser.error('--out must be new')
    cpus = None
    events = i1.EXPERIMENTS / 'iteration-105-ablation-runs' / doors[0]['run'] / f"seed-{doors[0]['run'].split('-')[2]}-events.jsonl"
    for line in events.read_text(encoding='utf-8').splitlines():
        row = json.loads(line)
        if row.get('event') == 'started':
            cpus = row.get('cpus'); break
    if os.name == 'nt' and cpus:
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.GetCurrentProcess.restype = ctypes.c_void_p
        k.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        if not k.SetProcessAffinityMask(k.GetCurrentProcess(), sum(1 << c for c in cpus)):
            raise ctypes.WinError(ctypes.get_last_error())
        os.environ['SPIRE_TARGET_CPUS'] = str(len(cpus))
    sdk = i1.ROOT.parents[2] / '.tools/dotnet'
    if (sdk / 'dotnet.exe').is_file():
        os.environ['PATH'] = str(sdk) + os.pathsep + os.environ.get('PATH', '')
        os.environ['DOTNET_ROOT'] = str(sdk)
    from spire_exact.native import game_data
    from spire_exact.planning.pool import NativePool, WorkerError
    from spire_exact.planning.resources import ResourcePlan, enclosing_job_memory_limit
    limit = enclosing_job_memory_limit()
    if not limit:
        parser.error('run inside tools/in_job.py')
    resources = ResourcePlan.detect(args.workers, 1, None, 1024, limit, shared=True)
    data = game_data(i1.ROOT / 'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64')
    args.out.mkdir(parents=True, exist_ok=True)
    R = args.out / 'requests'
    report = {'schema': SCHEMA, 'started': time.strftime('%Y-%m-%dT%H:%M:%S'), 'cpus': cpus,
              'runtime_profile': args.runtime_profile, 'harness_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'doors': []}
    errors = []

    def run_all(pool, jobs):
        futures = [(tag, pool.submit(req, R / tag, args.timeout, fresh=fresh)) for tag, req, fresh in jobs]
        out = {}
        for tag, future in futures:
            try:
                out[tag] = future.result()[0]
            except (WorkerError, OSError, ValueError, KeyError) as exc:
                errors.append((tag, repr(exc))); out[tag] = None
            print(tag, 'error' if out[tag] is None else out[tag].get('status'), flush=True)
        return out

    with NativePool(data, args.out / 'workers', resources, runtime_profile=args.runtime_profile,
                    memory_policy='shared') as pool:
        caps = pool.stamp.get('capabilities') or []
        need = {'gate_plans.final_boss_ordinal', 'replay.stop_after_combat', 'replay.state_dump'}
        if not need <= set(caps):
            raise SystemExit(f'host lacks {sorted(need - set(caps))}')
        report['host_sha256'], report['capabilities'] = pool.stamp['host_sha256'], caps
        # Wave 1: setup (game start -> F1 door, map checkpoints on) and the alternative F1 lines.
        wave = []
        for d in doors:
            setup = base(d['request']); setup['history'] = d['trace'][:d['f1_index']]
            setup['capture_checkpoints'] = True; setup['dump_state_at_end'] = True
            wave.append((d['tag'] + '--setup', setup, True))
            for rank, (j, member) in enumerate(d['alternatives']):
                wave.append((d['tag'] + f'--gen{rank}', i1.make(d['request'], {'trace': d['trace']}, {'index': d['f1_index']}, member), False))
        got = run_all(pool, wave)
        # Wave 2: the forks.
        wave, lines = [], {}
        for d in doors:
            cps = sorted((R / (d['tag'] + '--setup') / 'data/checkpoints').glob('map-*.json*'))
            cps = [p for p in cps if int(p.name.split('-')[1].split('.')[0]) <= d['f1_index']]
            d['checkpoint'] = str(cps[-1]) if cps else None
            post = d['trace'][d['after']:d['f2_index']]
            lines[d['tag']] = {'default': d['trace'][:d['f2_index']]}
            d['f1_lost'] = []
            for rank in range(len(d['alternatives'])):
                g = got.get(d['tag'] + f'--gen{rank}')
                if g is not None and g.get('status') == 'TERMINAL':
                    d['f1_lost'].append(f'alt{rank}')   # this member loses F1: the run ends, no F2 to fork
                elif g is not None:
                    lines[d['tag']][f'alt{rank}'] = g['trace'] + post
            for line, history in lines[d['tag']].items():
                t = f"{d['tag']}--{line}"
                wave.append((t + '--scratch', fork_request(d['request'], history), True))
                wave.append((t + '--pooled', fork_request(d['request'], history), False))
                if d['checkpoint']:
                    for kind, dump in (('ckpt', True), ('ckpt-solve', False)):
                        req = fork_request(d['request'], history, dump); req['checkpoint'] = d['checkpoint']
                        wave.append((t + '--' + kind, req, False))
        for d in doors[:args.repeats]:
            wave.append((d['tag'] + '--default--repeat', fork_request(d['request'], lines[d['tag']]['default']), True))
        got.update(run_all(pool, wave))

    def dump_of(tag):
        p = R / tag / 'data/state-dump.json'
        return json.loads(p.read_text(encoding='utf-8')) if p.exists() else None

    for d in doors:
        row = {k: d.get(k) for k in ('tag', 'run', 'eval', 'door', 'f1_index', 'after', 'f2_index', 'checkpoint', 'f1_lost')}
        row['setup_wall'] = wall(R / (d['tag'] + '--setup'))[0] if got.get(d['tag'] + '--setup') else None
        row['lines'] = {}
        for line, history in lines[d['tag']].items():
            t = f"{d['tag']}--{line}"
            c = {'history_length': len(history)}
            sc, po, ck = dump_of(t + '--scratch'), dump_of(t + '--pooled'), dump_of(t + '--ckpt')
            if sc is None:
                c['error'] = 'no scratch dump'; row['lines'][line] = c; continue
            rs, rows = last_decision_row(R / (t + '--scratch'))
            c['hazards'] = {'test_rng_injector_null': all(v is None for v in sc['test_rng_injector'].values()),
                            'shuffle_hooks': sc['shuffle_hooks'], 'dump_side_effect_free': sc['dump_side_effect_free'],
                            'reseeds': reseeds(rows), 'run_error': sc.get('run_error')}
            for other, dump in (('pooled', po), ('ckpt', ck)):
                if dump is None:
                    c[other] = {'error': 'no dump'}; continue
                c[other] = compare_dumps(sc, dump, rs, last_decision_row(R / (t + '--' + other))[0])
            if line == 'default':
                obs = got[t + '--scratch'].get('observation')
                c['entry_equal_record'] = record_only(obs) == record_only(d['f2_entry'])
                solve = got.get(t + '--ckpt-solve')
                if solve is not None:
                    groups = boss_search_groups(solve)
                    call = first_call(groups[0]) if groups else []
                    c['f2_forecasts_equal_record'] = [{k: (r.get('forecast') or {}).get(k) for k in i1.FORECAST}
                                                      for r in call] == d['f2_recorded']
                    c['f2_nodes_equal_record'] = [r.get('expanded_nodes') for r in call] == d['f2_recorded_nodes']
                rep = R / (t + '--repeat') / 'data/state-dump.json'
                if rep.exists():
                    c['repeat_byte_identical'] = rep.read_bytes() == (R / (t + '--scratch') / 'data/state-dump.json').read_bytes()
            if got.get(t + '--ckpt-solve'):
                w, st = wall(R / (t + '--ckpt-solve'))
                us = lambda *names: sum((st.get(n) or {}).get('us', 0) for n in names) / 1e6
                restore = us('run_setup_or_restore')
                solve = us('beam_search', 'combat_advisor', 'new_native_execute', 'continuation_compare', 'candidate_select')
                boot = us('bootstrap')
                c['timing'] = {'restore': round(restore, 3), 'f1_entry': round(w - boot - restore - solve, 3),
                               'f2_solve': round(solve, 3), 'integrated': round(w - boot, 3), 'bootstrap': round(boot, 3),
                               'checkpoint_capture': round(us('checkpoint_capture'), 3),
                               'scratch_fork_dump': round(wall(R / (t + '--scratch'))[0], 3)}
            row['lines'][line] = c
        report['doors'].append(row)
    report['errors'] = errors
    report['finished'] = time.strftime('%Y-%m-%dT%H:%M:%S')
    (args.out / 'fork-check.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
