"""G2-0 I-1 (106 F0-1, F0-2): does one F1 gate member, run alone, reproduce
the forecast it reported inside the multi-member plan, and does F1 then play
out as forecast?

P1 (planning/plugins/reroll.py) re-solves F1 with a one-member 'FinalBoss1'
plan. That is only a re-roll of the recorded alternative if the member alone
forecasts what it forecast next to the others. Two things can differ: the
members of one request share a solver session (BeamAdvisor.Apply keeps it
while portfolio, widths and budget are equal, and boss and normal budgets are
equal in 105, so the session also spans the fights searched before F1), and
continuations are reused across turns.

Sample (fixed, before any run): iteration-105 arms A / A2 doors of
g2/door-inventory.json with an alternative F1 winner; per seed the door with
the smallest sha256('i1:' + seed + ':' + door key); in it the alternatives in
P1's own order (f1_alternatives, up to --per-door). Every request replays the
recorded trace up to the F1 entry from the game start (no checkpoint: the 105
checkpoints belong to another host), with stop_after_combat, as three arms:

  full      the recorded plan unchanged    -> all recorded member forecasts?
  deployed  FinalBoss1 = [deployed member] -> its recorded forecast?
  alt       FinalBoss1 = [alternative j]   -> its recorded forecast? F1 as forecast?

'full' separates the replay-from-start / new-host effect from the one-member
effect. Real job (P cores, the recording's placement), inside a Job for the
shared memory policy:

  python tools/in_job.py --job-mib 24576 tools/f1_member_check.py --out DIR [--per-door 1] [--dry-run]
"""
from pathlib import Path
import argparse
import ctypes
import gzip
import hashlib
import json
import os
import sys
import time
from copy import deepcopy

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
EXPERIMENTS = ROOT.parent
sys.path.insert(0, str(ROOT))
from spire_exact.planning.gatemodel import boss_fight_rows  # noqa: E402
from spire_exact.planning.plugins.reroll import boss_search_groups, f1_alternatives, first_call, last_act_boss_plan, same_member  # noqa: E402

SCHEMA = 'spire-f1-member-check/v1'
FORECAST = ('won', 'survives', 'hp', 'potions', 'death_turn', 'end_turn', 'enemy_hp', 'enemy_deaths')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def read(path):
    if path.suffix == '.gz':
        with gzip.open(path, 'rt', encoding='utf-8') as f:
            return json.load(f)
    return json.loads(path.read_text(encoding='utf-8'))


def decision_of(ev):
    gz = ev / 'data/decision.json.gz'
    return read(gz if gz.exists() else ev / 'data/decision.json')


def sample(inventory, runs_dir, per_door):
    """[(door, run, eval dir, request, decision, f1 row, deployed record, [alternative records])], seed order."""
    by_seed = {}
    for name, run in inventory['runs'].items():
        for door in run['doors']:
            if door.get('alt_members'):
                by_seed.setdefault(run['summary']['seed'], []).append((name, door))
    picked = []
    for seed in sorted(by_seed, key=int):
        name, door = min(by_seed[seed], key=lambda x: hashlib.sha256(f"i1:{seed}:{x[1]['key']}".encode()).hexdigest())
        ev = runs_dir / name / f'seed-{seed}' / door['eval']
        request, decision = read(ev / 'request.json'), decision_of(ev)
        f1, alternatives = f1_alternatives(decision, len(request['history']), per_door)
        if f1 is None or not alternatives:
            continue          # inventory door P1 would not re-roll (e.g. forecasts equal to the deployed one)
        call = first_call(boss_search_groups(decision)[[r['index'] for r in boss_fight_rows(decision)
                                                         if r['index'] >= len(request['history'])].index(f1['index'])])
        deployed = next(r for r in call if r.get('selected'))
        picked.append((door, name, ev, request, decision, f1, deployed, alternatives, call))
    return picked


def make(request, decision, f1, member):
    """Replay the recorded trace to the F1 entry from the game start, stop after F1."""
    out = deepcopy(request)
    out.pop('checkpoint', None)
    out.pop('out', None)
    out['history'] = decision['trace'][:f1['index']]
    out['stop_after_combat'] = True
    if member is not None:
        plans = out['advisor']['gate_plans']
        plans['FinalBoss1'] = {'members': [deepcopy(member)], 'select': 'first_win'}
    return out


def outcome(result, f1_index):
    """F1 call forecasts and the F1 fight as played."""
    groups = boss_search_groups(result)
    rows = boss_fight_rows(result)
    f1 = next((r for r in rows if r['index'] == f1_index), None)
    call = first_call(groups[0]) if groups else []
    obs = result.get('observation') or {}
    evidence = result.get('decision_evidence') or []
    turns = [int((e.get('observation') or {}).get('turn') or 0) for e in evidence[f1_index:]
             if (e.get('observation') or {}).get('turn') is not None]
    # stop_after_combat: a won F1 ends at the first menu after it (the observation).
    won = f1 is not None and f1['lost'] is None and f1['outcome'] > 1
    entry = len((f1 or {}).get('entry', {}).get('potions') or [])
    return {'forecasts': [{k: (r.get('forecast') or {}).get(k) for k in FORECAST} for r in call],
            'members': [r.get('member') for r in call], 'selected': [bool(r.get('selected')) for r in call],
            'expanded_nodes': [r.get('expanded_nodes') for r in call],
            'f1_entry_found': f1 is not None, 'trace_length': len(result.get('trace') or []),
            'played': {'won': won, 'hp': int(obs.get('hp') or 0) if won else None, 'entry_potions': entry,
                       'potions_used': entry - len(obs.get('potions') or []) if won else None,
                       'end_turn': max(turns) if turns else None, 'status': result.get('status')},
            'searches_in_f1': sum(len(g) for g in groups[:1])}


def recorded(record):
    return {k: (record.get('forecast') or {}).get(k) for k in FORECAST}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--inventory', type=Path, default=EXPERIMENTS / 'g2/door-inventory.json')
    parser.add_argument('--runs', type=Path, default=EXPERIMENTS / 'iteration-105-ablation-runs')
    parser.add_argument('--per-door', type=int, default=1)
    parser.add_argument('--workers', type=int, default=7)
    parser.add_argument('--timeout', type=float, default=1800)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    picked = sample(read(args.inventory), args.runs, args.per_door)
    jobs = []
    for door, name, ev, request, decision, f1, deployed, alternatives, call in picked:
        plan = last_act_boss_plan(request['advisor'])
        tag = f"{name}--{door['eval']}"
        base = {'run': name, 'eval': door['eval'], 'door': door['key'], 'f1_index': f1['index'],
                'recorded': [recorded(r) for r in call], 'recorded_members': [r.get('member') for r in call]}
        jobs.append((tag + '--full', {**base, 'arm': 'full', 'member_index': None}, make(request, decision, f1, None)))
        j = int(deployed['member_index'])
        assert same_member(plan['members'][j], deployed.get('member'))
        jobs.append((tag + '--deployed', {**base, 'arm': 'deployed', 'member_index': j, 'want': recorded(deployed)},
                     make(request, decision, f1, plan['members'][j])))
        for rank, alt in enumerate(alternatives):
            j = int(alt['member_index'])
            assert same_member(plan['members'][j], alt.get('member'))
            jobs.append((tag + f'--alt{rank}', {**base, 'arm': 'alt', 'rank': rank, 'member_index': j, 'want': recorded(alt)},
                         make(request, decision, f1, plan['members'][j])))
    alts = sum(1 for _, meta, _ in jobs if meta['arm'] == 'alt')
    print(f'{len(picked)} doors, {alts} alternatives, {len(jobs)} requests', flush=True)
    if args.dry_run:
        for tag, meta, _ in jobs:
            print(tag, meta['member_index'], meta.get('want'))
        return
    if args.out.exists() and any(args.out.iterdir()):
        parser.error('--out must be new')
    # P-core placement of the 105 A recordings (7 workers), as speed_calibration uses.
    cpus = None
    events = args.runs / jobs[0][1]['run'] / f"seed-{jobs[0][1]['run'].split('-')[-1]}-events.jsonl"
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
    sdk = ROOT.parents[2] / '.tools/dotnet'
    if (sdk / 'dotnet.exe').is_file():
        os.environ['PATH'] = str(sdk) + os.pathsep + os.environ.get('PATH', '')
        os.environ['DOTNET_ROOT'] = str(sdk)
    from spire_exact.native import game_data
    from spire_exact.planning.pool import NativePool, WorkerError
    from spire_exact.planning.resources import ResourcePlan, enclosing_job_memory_limit
    limit = enclosing_job_memory_limit()
    if not limit:
        parser.error('run inside tools/in_job.py (shared memory policy needs an enclosing job limit)')
    resources = ResourcePlan.detect(args.workers, 1, None, 1024, limit, shared=True)
    if resources.workers != args.workers:
        raise SystemExit('the worker count cannot fit')
    data = game_data(ROOT / 'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64')
    args.out.mkdir(parents=True, exist_ok=True)
    report = {'schema': SCHEMA, 'started': time.strftime('%Y-%m-%dT%H:%M:%S'), 'cpus': cpus,
              'resources': resources.as_dict(), 'harness_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'doors': len(picked), 'alternatives': alts}
    rows = []
    with NativePool(data, args.out / 'workers', resources, memory_policy='shared') as pool:
        caps = pool.stamp.get('capabilities') or []
        if 'gate_plans.final_boss_ordinal' not in caps or 'replay.stop_after_combat' not in caps:
            raise SystemExit(f'host lacks FinalBoss1 / stop_after_combat: {caps}')
        report['host_sha256'], report['capabilities'] = pool.stamp['host_sha256'], caps
        futures = [(tag, meta, pool.submit(req, args.out / 'requests' / tag, args.timeout)) for tag, meta, req in jobs]
        for tag, meta, future in futures:
            row = {'tag': tag, **meta}
            try:
                result, _ = future.result()
                row['got'] = outcome(result, meta['f1_index'])
                perf = json.loads((args.out / 'requests' / tag / 'data/performance.json').read_text(encoding='utf-8'))
                row['wall_seconds'] = round(perf['wall_us'] / 1e6, 3)
            except (WorkerError, OSError, ValueError, KeyError, IndexError) as exc:
                row['error'] = repr(exc)
            rows.append(row)
            print(tag, 'error' if 'error' in row else row['got']['forecasts'], flush=True)
    report['rows'] = rows
    report['finished'] = time.strftime('%Y-%m-%dT%H:%M:%S')
    (args.out / 'f1-member-check.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
