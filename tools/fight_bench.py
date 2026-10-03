"""Fight-level native benchmark on retained entries: same request, same actions, how fast?

    collect  pick distinct fight entries from a completed seed directory
    run      execute every entry under one configuration with the production NativePool
    compare  pair two result files: identical action traces? speed ratio?

Each case replays the retained action prefix up to the first decision of one fight and lets the
host play that fight to its end (stop at the fight's floor). The new actions are hashed, so two
configurations that must not change behaviour (runtime settings, solver refactors) can be checked
action-for-action, and two that may change it (budgets, gate plans) can be compared by outcome.

Results are component evidence only: never a campaign result, a win claim or a bound. Do not run
this next to a seed job on the same CPU class; timings from different core classes are not
comparable (AGENTS.md 3.2).

    python tools/fight_bench.py collect --seed-dir D:/.../seed-10101010 --out cases.json
    python tools/fight_bench.py run --cases cases.json --out D:/.../bench-a --gate-preset escalate
    python tools/fight_bench.py compare D:/.../bench-a/results.jsonl D:/.../bench-b/results.jsonl
"""
from pathlib import Path
import argparse, ctypes, gzip, hashlib, json, os, random, statistics, subprocess, sys, time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
GAME_FIELDS = ('status', 'phase', 'reason', 'value', 'observation', 'trace', 'decision_evidence', 'native_terminal_observed')


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def algorithm_request(request):
    """Normalize only the explicitly compared solver DLL, preserving other identities."""
    result = json.loads(json.dumps(request))
    advisor = result.get('advisor')
    if advisor and advisor.get('solver'):
        solver = Path(advisor.pop('solver')).resolve()
        advisor['binary_identity'] = {k: v for k, v in advisor.get('binary_identity', {}).items()
                                      if Path(k).resolve() != solver}
    return result


def _decision(seed_dir, label):
    path = Path(seed_dir) / label / 'data' / 'decision.json.gz'
    with gzip.open(path, 'rt', encoding='utf-8-sig') as stream:
        return json.load(stream)


def fights(decision, start=0):
    """Fights whose first combat decision is at trace index >= start."""
    trace = decision.get('trace') or []
    evidence = decision.get('decision_evidence') or []
    rows, current = [], None
    for index in range(min(len(trace), len(evidence))):
        row = evidence[index]
        obs = row.get('observation') or {}
        in_combat = row.get('phase') == 'combat' or (row.get('phase') == 'select_cards' and obs.get('turn') is not None)
        if in_combat:
            key = (obs.get('act'), obs.get('floor'))
            if current is None or current['key'] != key:
                current = {'key': key, 'cut': index, 'act': obs.get('act'), 'floor': obs.get('floor'), 'room': obs.get('room'),
                           'hp': obs.get('hp'), 'max_hp': obs.get('max_hp'), 'deck': len(obs.get('deck') or []),
                           'encounter': [e.get('id') for e in obs.get('enemies') or []]}
                rows.append(current)
    return [dict((k, v) for k, v in r.items() if k != 'key') for r in rows if r['cut'] >= start]


def collect(a):
    seed_dir = a.seed_dir.resolve()
    result = json.loads((seed_dir / 'result.json').read_text(encoding='utf-8-sig'))
    rooms = set(a.rooms.split(','))
    labels = []
    with (seed_dir / 'evaluations.jsonl').open(encoding='utf-8') as stream:
        for line in stream:
            try: row = json.loads(line)
            except ValueError: break
            if row.get('classification') in ('NATIVE_ROUTE_DEATH', 'NATIVE_WIN_CANDIDATE') and not row.get('cache_hit'):
                labels.append((row['label'], row.get('prefix_length') or 0))
    random.Random(a.rng).shuffle(labels)
    cases, seen, count = [], set(), {room: 0 for room in rooms}
    for label, prefix in labels:
        if all(count[room] >= a.per_room for room in rooms):
            break
        try: decision = _decision(seed_dir, label)
        except OSError: continue                       # retention removed the detailed trace
        for fight in fights(decision, prefix):
            room = fight['room']
            if room not in rooms or count[room] >= a.per_room:
                continue
            digest = hashlib.sha256(_canonical(decision['trace'][:fight['cut']])).hexdigest()
            if digest in seen:
                continue
            seen.add(digest); count[room] += 1
            cases.append({'id': f"{label[5:9]}-F{fight['floor']}-{room}", 'label': label, 'prefix_sha256': digest, **fight})
    document = {'schema': 'spire-fight-bench-cases/v1', 'seed_dir': str(seed_dir), 'context': result['context'],
                'rng': a.rng, 'cases': cases, 'scope': 'component benchmark entries; not a campaign panel'}
    a.out.write_text(json.dumps(document, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps({'cases': len(cases), 'by_room': count, 'out': str(a.out)}))


def _affinity(kind):
    if kind == 'inherit' or os.name != 'nt':
        return None
    from tools.cpu_topology import inventory, homogeneous_cpus
    rows = inventory()
    lowest = min(r['efficiency_class'] for r in rows)
    cpus, cls = homogeneous_cpus(rows, 16 if kind == 'p-smt' else 8, None if kind in ('p','p-smt') else lowest)
    k = ctypes.WinDLL('kernel32', use_last_error=True); k.GetCurrentProcess.restype = ctypes.c_void_p
    k.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    if not k.SetProcessAffinityMask(k.GetCurrentProcess(), sum(1 << i for i in cpus)):
        raise ctypes.WinError(ctypes.get_last_error())
    os.environ['SPIRE_TARGET_CPUS'] = str(len(cpus))
    return {'cpus': cpus, 'efficiency_class': cls}


def summarize(decision, cut, floor, transport):
    obs = decision.get('observation') or {}
    searches = (decision.get('advisor_metrics') or {}).get('searches') or []
    reason = decision.get('reason') or ''
    won = (decision.get('status') == 'BUDGET' and reason.startswith('candidate_horizon')) or (obs.get('floor') or 0) > floor \
        or (decision.get('value') or [0])[0] == 1
    wall = sum(s.get('wall_us') or 0 for s in searches) / 1e6
    nodes = sum(s.get('expanded_nodes') or 0 for s in searches)
    new = (decision.get('trace') or [])[cut:]
    perf = decision.get('performance') or {}
    phases = {}
    for search in searches:
        for name, metric in (search.get('phase_metrics') or {}).items():
            totals = phases.setdefault(name, {'wall_us': 0, 'allocated_bytes': 0})
            for field in totals:
                totals[field] += metric.get(field) or 0
    return {'status': decision.get('status'), 'won_fight': bool(won), 'hp_out': obs.get('hp') if won else None,
            'potions_out': sum(p is not None for p in obs.get('potions') or []) if won else None,
            'new_actions': len(new), 'trace_sha256': hashlib.sha256(_canonical(new)).hexdigest(),
            'game_sha256': hashlib.sha256(_canonical({k: decision.get(k) for k in GAME_FIELDS})).hexdigest(),
            'reported_time_boundary': any(s.get('time_boundary') for s in searches),
            'process_cycles': perf.get('cycles'), 'gc_configuration': perf.get('gc_configuration'),
            'phase_metrics': phases,
            'recycle_reason': transport.get('recycle_reason'), 'worker_job': transport.get('worker_job'),
            'searches': len(searches), 'nodes': nodes, 'search_wall': round(wall, 3),
            'nodes_per_s': round(nodes / wall) if wall else None,
            'gc_pause': round(sum(s.get('gc_pause_ms') or 0 for s in searches) / 1e3, 3),
            'allocated_gb': round(sum(s.get('allocated_bytes') or 0 for s in searches) / 2 ** 30, 3),
            'wall': round(transport['wall_seconds'], 3), 'peak_rss_mb': transport['peak_sampled_rss'] >> 20}


def run(a):
    if a.out.exists() and any(a.out.iterdir()):
        raise SystemExit('output must be new/empty; never overwrite prior evidence')
    sdk = ROOT.parent / '.tools/dotnet'
    if not (sdk / 'dotnet.exe').is_file():       # frozen workspaces live two levels below the main tree
        sdk = ROOT.parents[2] / '.tools/dotnet'
    if (sdk / 'dotnet.exe').is_file():
        os.environ['PATH'] = str(sdk) + os.pathsep + os.environ.get('PATH', ''); os.environ['DOTNET_ROOT'] = str(sdk)
    affinity = _affinity(a.cpus)
    from spire_exact.mode1 import NativeCampaignBackend, context
    from spire_exact.native import game_data
    from spire_exact.planning.pool import NativePool
    from spire_exact.planning.resources import ResourcePlan
    from spire_exact.planning.gates import preset
    document = json.loads(a.cases.read_text(encoding='utf-8'))
    c = document['context']
    ctx = context(c['seed'], c['character'], c['ascension'], c['unlocks'])
    data = game_data(a.game_dir or ROOT / 'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64')
    a.out.mkdir(parents=True, exist_ok=True)
    advisor = NativeCampaignBackend(ctx, a.out, data, 'combatsolver').advisor
    advisor.update(budget_ms=600000, boss_budget_ms=600000, dop=1, profile=a.profile, reuse_continuations=True,
                   complete_continuations_only=False, nodes=a.nodes, fix_consumed_block_compensation=True, quiet_diagnostics=True)
    if a.normal_nodes is not None: advisor['normal_nodes'] = a.normal_nodes
    plans = preset(a.gate_preset)['plans']
    if plans: advisor['gate_plans'] = plans
    if a.advisor_json: advisor.update(json.loads(a.advisor_json))
    if getattr(a, 'solver_dll', None):
        from spire_exact.planning.identity import advisor_identity
        advisor['solver'] = str(a.solver_dll.resolve())
        advisor['binary_identity'] = advisor_identity(advisor)
    resources = ResourcePlan.detect(a.workers, 1, a.worker_memory_mib, a.reserve_mib)
    if resources.workers != a.workers:
        raise SystemExit('Requested worker count cannot fit; refuse to compare a silently clamped pool')
    manifest = {'schema': 'spire-fight-bench/v2', 'cases': str(a.cases), 'cases_sha256': hashlib.sha256(a.cases.read_bytes()).hexdigest(), 'advisor': advisor,
                'runtime_profile': a.runtime_profile, 'resources': resources.as_dict(), 'affinity': affinity, 'repeat': a.repeat,
                'worker_memory_policy': a.worker_memory_policy, 'max_jobs': a.max_jobs, 'queue_policy': a.queue_policy,
                'counts_as_planner_result': False}
    (a.out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding='utf-8')
    cases = document['cases'][:a.limit] if a.limit else document['cases']
    started = time.perf_counter(); rows = []
    with NativePool(data, a.out / 'workers', resources, runtime_profile=a.runtime_profile,
                    memory_policy=a.worker_memory_policy, max_jobs=a.max_jobs, queue_policy=a.queue_policy) as pool, \
            (a.out / 'results.jsonl').open('a', encoding='utf-8') as sink:
        manifest['host_sha256'] = pool.stamp['host_sha256']
        manifest.update(pool.memory)
        (a.out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding='utf-8')
        pmu_profile=getattr(a,'pmu_profile',None);pmu_out=getattr(a,'pmu_out',None)
        warmups=getattr(a,'warmup_repeat',0)
        if bool(pmu_profile)!=bool(pmu_out):raise ValueError('Both PMU profile and output are required')
        if pmu_profile and not warmups:raise ValueError('PMU capture requires explicit warmup')
        if warmups:
            warm=[]
            for repeat in range(warmups):
                for index,case in enumerate(cases):
                    trace=_decision(document['seed_dir'],case['label'])['trace'][:case['cut']]
                    if hashlib.sha256(_canonical(trace)).hexdigest()!=case['prefix_sha256']:raise ValueError('Warmup prefix changed')
                    request={'seed':c['seed'],'character':c['character'],'ascension':c['ascension'],'unlocks':c['unlocks'],
                             'history':trace,'generate_candidate':True,'policy_seed':0,'max_decisions':case['cut']+3000,
                             'stop_at_floor':case['floor'],'capture_checkpoints':False,'low_io':True,'event_driven_settle':True,
                             'advisor':json.loads(json.dumps(advisor))}
                    warm.append(pool.submit(request,a.out/f'warm-{index:04d}-r{repeat}',a.timeout))
            for future in warm:future.result()  # Barrier: all processes are warm before tracing.
        session='SpireExactPMU';recording=False
        if pmu_profile:
            if pmu_out.exists():raise ValueError('Never overwrite a PMU trace')
            pmu_out.parent.mkdir(parents=True,exist_ok=True)
            command=['wpr.exe','-start',str(pmu_profile.resolve())+'!SpireGeneric','-filemode','-instancename',session,'-recordtempto',str(pmu_out.parent.resolve())]
            capture=subprocess.run(command,capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW)
            (a.out/'pmu-start.log').write_text(capture.stdout+capture.stderr,encoding='utf-8')
            if capture.returncode:raise RuntimeError('WPR PMU start failed; no measurement was submitted')
            recording=True
            manifest.update(pmu_profile=str(pmu_profile.resolve()),pmu_trace=str(pmu_out.resolve()),warmup_repeat=warmups,
                            measured_started_unix=time.time(),warm_worker_jobs=[w.jobs for w in pool.workers])
            (a.out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=1),encoding='utf-8')
        try:
            pending = []
            for repeat in range(a.repeat):
                for index, case in enumerate(cases):
                    trace = _decision(document['seed_dir'], case['label'])['trace'][:case['cut']]
                    if hashlib.sha256(_canonical(trace)).hexdigest() != case['prefix_sha256']:
                        raise SystemExit('retained prefix changed for ' + case['id'])
                    request = {'seed': c['seed'], 'character': c['character'], 'ascension': c['ascension'], 'unlocks': c['unlocks'],
                               'history': trace, 'generate_candidate': True, 'policy_seed': 0, 'max_decisions': case['cut'] + 3000,
                               'stop_at_floor': case['floor'], 'capture_checkpoints': False, 'low_io': True,
                               'event_driven_settle': True, 'advisor': json.loads(json.dumps(advisor))}
                    out = a.out / f'case-{index:04d}-r{repeat}'
                    pending.append((case, repeat, out, hashlib.sha256(_canonical(request)).hexdigest(),
                                    hashlib.sha256(_canonical(algorithm_request(request))).hexdigest(), pool.submit(request, out, a.timeout)))
            for case, repeat, out, request_hash, algorithm_hash, future in pending:
                row = {'case': case['id'], 'repeat': repeat, 'floor': case['floor'], 'room': case['room'], 'encounter': case['encounter'],
                       'request_sha256': request_hash, 'algorithm_request_sha256': algorithm_hash}
                try:
                    decision, _ = future.result()
                    row.update(summarize(decision, case['cut'], case['floor'], json.loads((out / 'transport.json').read_text())))
                except Exception as error:                      # a failed case is reported, never dropped
                    row['error'] = str(error)[:400]
                rows.append(row); sink.write(json.dumps(row, ensure_ascii=False) + '\n'); sink.flush()
        finally:
            if recording:
                manifest['measured_finished_unix']=time.time()
                stop=subprocess.run(['wpr.exe','-stop',str(pmu_out.resolve()),'-instancename',session],capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW)
                (a.out/'pmu-stop.log').write_text(stop.stdout+stop.stderr,encoding='utf-8')
                manifest['pmu_stop_exit']=stop.returncode
                (a.out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=1),encoding='utf-8')
                if stop.returncode:raise RuntimeError('Owned WPR capture did not stop successfully')
    good = [r for r in rows if 'error' not in r]
    nodes, wall = sum(r['nodes'] for r in good), sum(r['search_wall'] for r in good)
    print(json.dumps({'cases': len(rows), 'errors': len(rows) - len(good), 'wins': sum(r['won_fight'] for r in good),
                      'nodes': nodes, 'search_wall': round(wall, 1), 'nodes_per_s': round(nodes / wall) if wall else None,
                      'elapsed': round(time.perf_counter() - started, 1), 'out': str(a.out / 'results.jsonl')}))


def _load(path):
    rows = {}
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        row = json.loads(line)
        if 'error' not in row:
            rows.setdefault(row['case'], []).append(row)
    return rows


def compare(a):
    first, second = _load(a.a), _load(a.b)
    shared = sorted(set(first) & set(second))
    identical = [c for c in shared if first[c][0]['trace_sha256'] == second[c][0]['trace_sha256']]
    print(f'cases: {len(first)} vs {len(second)}, paired {len(shared)}, identical new-action traces {len(identical)}')
    for name, rows in (('A', first), ('B', second)):
        flat = [r for c in shared for r in rows[c]]
        nodes, wall = sum(r['nodes'] for r in flat), sum(r['search_wall'] for r in flat)
        unstable = sum(len({r['trace_sha256'] for r in rows[c]}) > 1 for c in shared)
        print(f'  {name}: wins {sum(rows[c][0]["won_fight"] for c in shared)}, nodes {nodes}, search wall {wall:.1f} s, '
              f'{nodes / wall if wall else 0:.0f} nodes/s, GC pause {sum(r["gc_pause"] for r in flat):.1f} s, '
              f'allocated {sum(r["allocated_gb"] for r in flat):.1f} GB, peak RSS {max((r["peak_rss_mb"] for r in flat), default=0)} MB, '
              f'cases differing between repeats {unstable}')
    ratios = [first[c][0]['search_wall'] / second[c][0]['search_wall'] for c in identical if second[c][0]['search_wall'] > 0]
    if ratios:
        print(f'  identical cases: median search-wall ratio A/B {statistics.median(ratios):.3f} (>1 means B is faster)')
    shown = 0
    for c in shared:
        x, y = first[c][0], second[c][0]
        if c not in identical and shown < a.show:
            shown += 1
            print(f'  differs {c}: A won {x["won_fight"]} hp {x["hp_out"]} nodes {x["nodes"]} | B won {y["won_fight"]} hp {y["hp_out"]} nodes {y["nodes"]}')
    changed = [c for c in shared if first[c][0]['won_fight'] != second[c][0]['won_fight']]
    print(f'  fight results flipped: {len(changed)} (A-only wins {sum(first[c][0]["won_fight"] for c in changed)}, '
          f'B-only wins {sum(second[c][0]["won_fight"] for c in changed)})')
    report = compare_exact(a.a, a.b, allow_solver_change=getattr(a, 'allow_solver_change', False))
    print(f'  strict all-repeat equivalence: {report["equivalent"]}; issues {len(report["issues"])}')
    if getattr(a, 'report', None):
        a.report.write_text(json.dumps(report, indent=2), encoding='utf-8')
    if getattr(a, 'strict', False) and not report['equivalent']:
        raise SystemExit(1)
    return report


def compare_exact(a, b, *, allow_solver_change=False):
    """No silently dropped errors, missing repeats or node/request differences.

    Digests index retained evidence, not native-state identity or a proof of
    equivalence beyond these complete exported game fields. Native outputs remain
    available for full byte comparisons and independent replay.
    """
    issues, arms = [], []
    for name, path in (('A', a), ('B', b)):
        rows = {}
        for index, line in enumerate(Path(path).read_text(encoding='utf-8').splitlines()):
            try:
                row = json.loads(line)
                key = (row['case'], row['repeat'])
            except (ValueError, KeyError, TypeError):
                issues.append(f'{name}: malformed row {index}'); continue
            if key in rows:
                issues.append(f'{name}: duplicate {key}')
            rows[key] = row
            if row.get('error') or row.get('status') not in ('BUDGET', 'DECISION', 'TERMINAL'):
                issues.append(f'{name}: failed or unsupported {key}')
            if row.get('reported_time_boundary'):
                issues.append(f'{name}: time boundary {key}')
        arms.append(rows)
    first, second = arms
    if not first or not second:
        issues.append('empty comparison')
    if first.keys() != second.keys():
        issues.append('case/repeat sets differ')
    paired = sorted(first.keys() & second.keys())
    same = 0
    fields = ('algorithm_request_sha256' if allow_solver_change else 'request_sha256', 'trace_sha256', 'game_sha256', 'nodes')
    for key in paired:
        if all(first[key].get(f) is not None and first[key].get(f) == second[key].get(f) for f in fields):
            same += 1
        else:
            issues.append(f'request/actions/game/nodes differ or are missing: {key}')
    for name, rows in zip(('A', 'B'), arms):
        reference = {}
        for key, row in rows.items():
            signature = tuple(row.get(f) for f in fields)
            if key[0] in reference and reference[key[0]] != signature:
                issues.append(f'{name}: repeat differs {key}')
            reference[key[0]] = signature
    return {'schema': 'spire-fight-bench-comparison/v1', 'equivalent': not issues,
            'solver_binary_change_allowed': allow_solver_change,
            'paired_repeats': len(paired), 'identical_repeats': same, 'issues': issues,
            'performance_promoted': False}


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest='command', required=True)
    s = sub.add_parser('collect'); s.add_argument('--seed-dir', type=Path, required=True); s.add_argument('--out', type=Path, required=True)
    s.add_argument('--rooms', default='Boss,Elite,Monster'); s.add_argument('--per-room', type=int, default=40)
    s.add_argument('--rng', type=int, default=20261001); s.set_defaults(fn=collect)
    s = sub.add_parser('run'); s.add_argument('--cases', type=Path, required=True); s.add_argument('--out', type=Path, required=True)
    s.add_argument('--game-dir', type=Path); s.add_argument('--workers', type=int, default=7)
    s.add_argument('--runtime-profile', default='server-large-gen0'); s.add_argument('--worker-memory-mib', type=int, default=1792)
    s.add_argument('--reserve-mib', type=int, default=1024); s.add_argument('--profile', default='Low')
    s.add_argument('--worker-memory-policy', choices=['hard', 'recycle-at-boundary'], default='hard')
    s.add_argument('--max-jobs', type=int, default=32)
    s.add_argument('--queue-policy', choices=['fifo', 'short-prefix-first'], default='fifo')
    s.add_argument('--nodes', type=int, default=60000); s.add_argument('--normal-nodes', type=int)
    s.add_argument('--gate-preset', default='none'); s.add_argument('--advisor-json', help='JSON object merged into the advisor block last')
    s.add_argument('--solver-dll', type=Path, help='Explicit project-owned solver variant; actual binary identity is recomputed')
    s.add_argument('--cpus', choices=['p', 'p-smt', 'e', 'inherit'], default='p', help='p: 8 P threads; p-smt: 16 P threads; e: functional checks')
    s.add_argument('--warmup-repeat',type=int,default=0)
    s.add_argument('--pmu-profile',type=Path);s.add_argument('--pmu-out',type=Path)
    s.add_argument('--repeat', type=int, default=1); s.add_argument('--limit', type=int); s.add_argument('--timeout', type=int, default=1200)
    s.set_defaults(fn=run)
    s = sub.add_parser('compare'); s.add_argument('a', type=Path); s.add_argument('b', type=Path); s.add_argument('--show', type=int, default=12)
    s.add_argument('--strict', action='store_true'); s.add_argument('--report', type=Path)
    s.add_argument('--allow-solver-change', action='store_true', help='Ignore only solver path and its fingerprint, retaining all other dependency identity')
    s.set_defaults(fn=compare)
    a = p.parse_args(); a.fn(a)


if __name__ == '__main__':
    main()
