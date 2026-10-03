"""Component checks of the native gate probe on retained runs (iteration-044, part C).

Runs a workspace's own host on a chosen E-core set. Every probe is synthetic: nothing here is a
win claim, a bound, or evidence about any real state.

  fidelity  no edits, real map entry: the probe must reproduce the retained fight
  synthetic no edits, EnterRoomDebug entry of the act's boss: compared with the retained fight
  table     one base entry per gate: every single-card edit, plus HP +-10; --samples N also re-fights
            the base under N combat RNG samples (the noise of one fight)
  readiness the first entry of each large deck family of a gate under --samples combat RNG samples

Results are read by tools/probe_signal.py (table) and tools/probe_readiness.py (readiness).
"""
from pathlib import Path
import argparse, ctypes, gzip, json, os, sys, time


def load_rows(seed_dir):
    rows = []
    for line in (seed_dir / 'evaluations.jsonl').open(encoding='utf-8'):
        try: rows.append(json.loads(line))
        except ValueError: break
    return rows


def load_decision(seed_dir, label):
    with gzip.open(seed_dir / label / 'data' / 'decision.json.gz', 'rt', encoding='utf-8') as fh:
        return json.load(fh)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--workspace', type=Path, required=True); p.add_argument('--seed-dir', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True); p.add_argument('--cpus', default='24-29'); p.add_argument('--workers', type=int, default=3)
    p.add_argument('--mode', choices=['fidelity', 'synthetic', 'table', 'readiness'], required=True)
    p.add_argument('--clusters', type=int, default=12, help='readiness: deck families probed per gate (largest first)')
    p.add_argument('--min-cluster', type=int, default=8, help='readiness: smallest family that is probed')
    p.add_argument('--per-class', type=int, default=1, help='fidelity: entries per gate and outcome class')
    p.add_argument('--gate', action='append', default=[], help='table: "act,ordinal" (repeatable)')
    p.add_argument('--base-window', type=int, default=24, help='table: base = closest lost entry among the first N entries of the gate')
    p.add_argument('--plan', default='retained', help='comma-separated: retained, e45-120k, e45-60k (table runs every plan)')
    p.add_argument('--limit', type=int, default=40, help='table: cards to try adding')
    p.add_argument('--samples', type=int, default=0, help='table: also probe the base under this many combat RNG samples (first plan only)')
    p.add_argument('--chunk', type=int, default=6)
    p.add_argument('--timeout', type=int, default=1800)
    p.add_argument('--max-evals', type=int, default=0, help='only scan the first N evaluations of the run (0 = all)')
    a = p.parse_args()
    if a.out.exists() and any(a.out.iterdir()):
        raise SystemExit('output must be new')
    workspace = a.workspace.resolve()
    lo, hi = (int(x) for x in a.cpus.split('-')); cpus = list(range(lo, hi + 1))
    k = ctypes.WinDLL('kernel32', use_last_error=True); k.GetCurrentProcess.restype = ctypes.c_void_p
    k.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    if not k.SetProcessAffinityMask(k.GetCurrentProcess(), sum(1 << i for i in cpus)):
        raise ctypes.WinError(ctypes.get_last_error())
    sdk = next(parent / '.tools/dotnet' for parent in workspace.parents if (parent / '.tools/dotnet/dotnet.exe').is_file())
    os.environ['PATH'] = str(sdk) + os.pathsep + os.environ.get('PATH', ''); os.environ['DOTNET_ROOT'] = str(sdk)
    os.environ['SPIRE_TARGET_CPUS'] = str(len(cpus)); sys.dont_write_bytecode = True
    sys.path.insert(0, str(workspace)); os.chdir(workspace)
    from spire_exact.mode1 import NativeCampaignBackend, context
    from spire_exact.planning.io import read_json
    from spire_exact.planning.pool import NativePool
    from spire_exact.planning.resources import ResourcePlan
    from spire_exact.planning.gatemodel import boss_fight_rows
    from spire_exact.planning.probes import gate_entries, probe_request, probe_outcome, edit_candidates, offered_cards
    data = workspace / 'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
    seed_dir = a.seed_dir.resolve()
    rows = load_rows(seed_dir)
    if a.max_evals: rows = rows[:a.max_evals]
    a.out.mkdir(parents=True, exist_ok=True)
    first = read_json(seed_dir / rows[0]['label'] / 'request.json')
    ctx = context(first['seed'], first['character'], first['ascension'], first['unlocks'])
    local = NativeCampaignBackend(ctx, a.out / 'advisor', data, 'combatsolver').advisor

    def rebased(request):
        """The retained request with this workspace's solver binaries (same pinned build, other path)."""
        request = json.loads(json.dumps(request))
        for key in ('solver', 'harness', 'dependency_dirs', 'binary_identity'):
            request['advisor'][key] = local[key]
        return request

    patches = {'retained': None,
               'e45-120k': {'gate_plans': {'Boss': {'members': [{'mode': 'Evaluate', 'beam': 45, 'nodes': 120000}], 'select': 'auto'}}},
               'e45-60k': {'gate_plans': {'Boss': {'members': [{'mode': 'Evaluate', 'beam': 45, 'nodes': 60000}], 'select': 'auto'}}}}
    plans = a.plan.split(',')
    if any(name not in patches for name in plans): raise SystemExit('unknown plan')

    # Every distinct boss entry that its own evaluation had to search, in absorption order.
    from collections import Counter
    entries = []                                   # (label, entry dict)
    seen = set()
    offered_by_label = {}                          # cards each evaluation's own (not replayed) decisions offered
    order = []
    for row in rows:
        if row.get('classification') not in ('NATIVE_ROUTE_DEATH', 'NATIVE_WIN_CANDIDATE') or row.get('cache_hit'):
            continue
        try: decision = load_decision(seed_dir, row['label'])
        except OSError: continue
        order.append(row['label'])
        offered_by_label[row['label']] = offered_cards(decision, since=row.get('prefix_length') or 0)
        for entry in gate_entries(decision):
            if entry['index'] < (row.get('prefix_length') or 0): continue
            key = json.dumps(decision['trace'][:entry['index']], sort_keys=True)
            if key in seen: continue
            seen.add(key)
            entries.append({'label': row['label'], 'kind': row['kind'], 'gate': entry['gate'], 'enter': entry['enter'], 'index': entry['index'],
                            'outcome': entry['outcome'], 'lost': entry['lost'], 'encounter': (entry['entry'].get('enemies') or [{}])[0].get('id'),
                            'hp': entry['entry'].get('hp'), 'max_hp': entry['entry'].get('max_hp'), 'deck': len(entry['entry'].get('deck') or []),
                            'cards': Counter(c['id'] for c in entry['entry'].get('deck') or []), 't': row.get('completed_wall_seconds')})
        if a.mode in ('fidelity', 'synthetic') and len(entries) > 4000: break
    gates = sorted({tuple(e['gate']) for e in entries})
    print('distinct searched boss entries: %d; gates: %s' % (len(entries), {g: sum(tuple(e['gate']) == g for e in entries) for g in gates}), flush=True)

    jobs = []                                       # (tag dict, request)
    cache = {}

    def decision_of(label):
        if label not in cache:
            cache.clear(); cache[label] = (load_decision(seed_dir, label), rebased(read_json(seed_dir / label / 'request.json')))
        return cache[label]

    def add(tag, entry, edits=(), plan=None, **options):
        plan = plan or plans[0]
        decision, request = decision_of(entry['label'])
        jobs.append(({**tag, 'plan': plan, 'label': entry['label'], 'gate': list(entry['gate']), 'encounter': entry['encounter'],
                      'retained_outcome': entry['outcome'], 'retained_lost': entry['lost']},
                     probe_request(request, decision['trace'], entry, edits, advisor_patch=patches[plan], **options)))

    def classes(gate):
        mine = [e for e in entries if tuple(e['gate']) == gate]
        total = max((e['lost'][1] for e in mine if e['lost']), default=0.0)
        def scale(e): return e['outcome'] if e['lost'] is None else (min(1.0, e['lost'][0] / total) if total else 0.0)
        return mine, scale

    if a.mode in ('fidelity', 'synthetic'):
        for gate in gates:
            mine, scale = classes(gate)
            # gate retries re-solve with a heavier plan: the retained request carries that plan, so they are fine too
            groups = {'passed': [e for e in mine if e['lost'] is None], 'near': [e for e in mine if e['lost'] is not None and scale(e) >= 0.5],
                      'far': [e for e in mine if e['lost'] is not None and scale(e) < 0.5]}
            for name, group in groups.items():
                step = max(1, len(group) // a.per_class)
                for entry in group[::step][:a.per_class]:
                    add({'class': name, 'mode': a.mode}, entry)
                    if a.mode == 'synthetic':
                        jobs[-1][1]['probe']['enter'] = {'kind': 'encounter', 'boss': entry['gate'][1]}
    elif a.mode == 'readiness':
        # Deck families of a gate (greedy, multiset Jaccard >= 0.75, absorption order). The first
        # entry of a family is probed under several combat RNG samples; the other members are the
        # real "re-rolls" of nearly the same deck that the probe mean is compared with afterwards.
        def alike(x, y):
            union = sum((x | y).values())
            return union == 0 or 100 * sum((x & y).values()) >= 75 * union
        families = {}
        for text in a.gate or ['%d,%d' % g for g in gates]:
            gate = tuple(int(x) for x in text.split(','))
            mine, scale = classes(gate)
            clusters = []
            for entry in mine:
                for cluster in clusters:
                    if alike(entry['cards'], cluster[0]['cards']):
                        cluster.append(entry); break
                else:
                    clusters.append([entry])
            chosen = sorted((c for c in clusters if len(c) >= a.min_cluster), key=len, reverse=True)[:a.clusters]
            print('gate %s: %d entries in %d families; probing %d families of at least %d entries' % (gate, len(mine), len(clusters), len(chosen), a.min_cluster), flush=True)
            for number, cluster in enumerate(chosen):
                for sample in range(1, a.samples + 1):
                    add({'mode': 'readiness', 'family': number, 'sample': sample}, cluster[0], rng=sample)
            families['%d,%d' % gate] = [{'family': number, 'members': [{'label': e['label'], 'outcome': e['outcome'], 'lost': e['lost'], 'hp': e['hp'],
                                                                   'max_hp': e['max_hp'], 'deck': e['deck'], 't': e['t']} for e in cluster]}
                                         for number, cluster in enumerate(chosen)]
        (a.out / 'families.json').write_text(json.dumps(families), encoding='utf-8')
    else:
        for text in a.gate:
            gate = tuple(int(x) for x in text.split(','))
            mine, scale = classes(gate)
            window = mine[:a.base_window]
            lost = [e for e in window if e['lost'] is not None]
            base = max(lost, key=scale) if lost else min(window, key=lambda e: e['outcome'])
            print('base entry: %s gate %s %s outcome %.3f (window %d)' % (base['label'], gate, base['encounter'], scale(base), a.base_window), flush=True)
            decision, _ = decision_of(base['label'])
            entry_obs = decision['decision_evidence'][base['index']]['observation']
            # Cards the run had offered when the base was absorbed (every decision counted once).
            offered = Counter()
            for label in order[:order.index(base['label']) + 1]:
                offered.update(offered_by_label[label])
            candidates = edit_candidates(entry_obs, offered, a.limit)
            hp, max_hp = int(float(entry_obs['hp'])), int(float(entry_obs['max_hp']))
            for plan in plans:
                add({'edit': 'base'}, base, plan=plan)
                for label, edits in candidates:
                    add({'edit': label}, base, edits, plan=plan)
                for delta in (-10, 10):
                    if 1 <= hp + delta <= max_hp:
                        add({'edit': '#hp%+d' % delta}, base, plan=plan, hp=hp + delta)
            # Noise: the same deck under other combat RNG samples, and a few edits under three samples each.
            for sample in range(1, a.samples + 1):
                add({'edit': 'base', 'sample': sample}, base, rng=sample)
            if a.samples:
                for label, edits in [c for c in candidates if c[0].startswith('card:')][:6]:
                    for sample in range(1, 4):
                        add({'edit': label, 'sample': sample}, base, edits, rng=sample)
    print('probes to run: %d' % len(jobs), flush=True)
    resources = ResourcePlan.detect(a.workers, 1, 1792, 1024)
    manifest = {'schema': 'spire-probe-bench/v1', 'workspace': str(workspace), 'seed_dir': str(seed_dir), 'mode': a.mode, 'plan': a.plan,
                'cpus': cpus, 'resources': resources.as_dict(), 'probes': len(jobs), 'counts_as_planner_result': False, 'synthetic': True}
    (a.out / 'manifest.json').write_text(json.dumps(manifest, indent=1), encoding='utf-8')
    started = time.perf_counter()
    with NativePool(data, a.out / 'workers', resources, runtime_profile='server-large-gen0') as pool, (a.out / 'results.jsonl').open('a', encoding='utf-8') as sink:
        manifest['host_sha256'] = pool.stamp['host_sha256']
        (a.out / 'manifest.json').write_text(json.dumps(manifest, indent=1), encoding='utf-8')
        pending = []
        for start in range(0, len(jobs), a.chunk):
            chunk = jobs[start:start + a.chunk]
            pending.append((chunk, pool.submit_probes([r for _, r in chunk], a.out / ('batch-%04d' % (start // a.chunk)), a.timeout)))
        for chunk, future in pending:
            try: results = future.result()
            except Exception as error: results = [(None, str(error))] * len(chunk)
            for (tag, request), (decision, error) in zip(chunk, results):
                row = dict(tag)
                if decision is None:
                    row['error'] = (error or '')[:300]
                else:
                    outcome = probe_outcome(decision)
                    perf = decision.get('performance') or {}
                    row.update(status=decision.get('status'), reason=(decision.get('reason') or '')[:300] or None, probe=decision.get('probe'),
                               result=outcome, wall=round((perf.get('wall_us') or 0) / 1e6, 2), prefix=len(request['history']),
                               replayed=(perf.get('counters') or {}).get('prefix_replayed_actions'),
                               searches=len((decision.get('advisor_metrics') or {}).get('searches') or []))
                row['elapsed'] = round(time.perf_counter() - started, 1)
                sink.write(json.dumps(row, ensure_ascii=False) + '\n'); sink.flush()
        manifest['pool_stats'] = pool.stats
    manifest['elapsed'] = round(time.perf_counter() - started, 1)
    (a.out / 'manifest.json').write_text(json.dumps(manifest, indent=1), encoding='utf-8')
    print('done in %.0f s' % manifest['elapsed'])


if __name__ == '__main__':
    main()
