"""Search core.

One loop: the allocator fills the free worker slots from the request sources,
the evaluator dispatches and collects, each collected result is absorbed (core
bookkeeping first, then every mechanism in order), and the run report is
published. The loop holds no mechanism of its own (mechanisms.py); optional
mechanisms are plugins (plugins/__init__.py lists the five hooks).

Behaviour without plugins is frozen-i054a-shm's search.solve():
tools/replay_golden.py checks every request and the final report against
recorded real-game runs.
"""
from __future__ import annotations
from collections import Counter, deque

from ..canonical import canonical, ContractError
from ..mode1 import OBJECTIVE, is_winning_candidate
from .archive import DiverseFrontier, utility, classify_failure, failure_combat_prefix
from .io import write_json
from .policies import NATIVE
from .repairs import RepairQueue
from .mechanisms import CombatProbe, Deepen, GateProbes, GateRetry
from .plugins.context import PluginContext

# Records that do not count as valid evaluations in the run report.
INVALID = frozenset({'UNKNOWN', 'MECHANISM_OR_HOST_GAP', 'RESTORE_OR_REPLAY_MISMATCH', 'TIMEOUT', 'RESOURCE_LIMIT',
                     'NATIVE_CRASH', 'INVALID_STATE', 'SEARCH_CANCELLED', 'SYNTHETIC_PROBE'})


class Source:
    """One request source of the allocator.

    `pop(slot)` returns a request spec or None (nothing to offer now).
    `pending()`, when given, must be true before `pop` is asked. With a
    `share`, the source fills a slot only while the evaluations of its `kinds`
    (scheduled plus already chosen in this fill) stay below that fraction of
    everything scheduled, counting the slot being filled; before its first
    evaluation it is always allowed. Allocation only."""

    def __init__(self, name, pop, *, pending=None, share=None, kinds=()):
        self.name, self.pop, self.pending, self.share, self.kinds = name, pop, pending, share, tuple(kinds)

    def eligible(self, s, specs):
        if self.pending is not None and not self.pending():
            return False
        if self.share is None:
            return True
        used = sum(s.scheduled[kind] for kind in self.kinds) + sum(spec['kind'] in self.kinds for spec in specs)
        return used == 0 or used < self.share * (sum(s.scheduled.values()) + len(specs) + 1)


class PriorityAllocator:
    """Fills each free worker slot from the first source that offers a
    request. frozen-i054a order (default_sources): urgent requests; a failure
    repair while repairs hold under a fifth of everything scheduled (always,
    before the first one); plugin sources; the strategic scheduler; a restart
    when the scheduler has nothing."""
    REPAIRS = ('combat_probe', 'gate_retry')
    REPAIR_SHARE = .2

    def __init__(self, sources):
        self.sources = list(sources)

    def fill(self, s, slots):
        specs = []
        for slot in range(slots):
            for source in self.sources:
                if source.eligible(s, specs):
                    spec = source.pop(slot)
                    if spec is not None:
                        specs.append(spec)
                        break
        return specs


def default_sources(s, extra=()):
    """frozen-i054a sources of a search `s`, with plugin sources `extra`
    between the failure repairs and the strategic scheduler. The restart
    source always offers a request, so every slot is filled."""
    return [Source('urgent', lambda slot: s.urgent.popleft(), pending=lambda: bool(s.urgent)),
            Source('repairs', lambda slot: s.repairs.popleft(), pending=lambda: bool(s.repairs),
                   share=PriorityAllocator.REPAIR_SHARE, kinds=PriorityAllocator.REPAIRS),
            *extra,
            Source('scheduler', lambda slot: s.macro_spec()),
            Source('restart', s.restart_spec)]


class Search:
    def __init__(self, ctx, out, pool, config, *, evaluator_class, advisor=None, initial_prefix=None,
                 import_checkpoints=(), progress=None, scope_prefix=None, stop_floor=None, plugins=(), component=None):
        from .strategy import DepthFirstScheduler
        from .native_tree import NativeTreeScheduler
        from .incumbent import IncumbentScheduler
        from .indexed_strategy import IndexedStrategicScheduler
        from .focus import FocusScheduler
        from .gatemodel import GateModels
        if ctx.get('objective') != OBJECTIVE:
            raise ContractError('P5 supports mode1 full-information Boolean objective only')
        if (out / 'result.json').exists():
            raise ContractError('use a fresh output directory')
        out.mkdir(parents=True, exist_ok=True)
        if component is not None:
            # Component mode (component.py): the door is the scope and the start.
            if initial_prefix or scope_prefix is not None or stop_floor is not None:
                raise ContractError('a component sets its own prefix, scope and stop floor')
            initial_prefix, scope_prefix, stop_floor = component.prefix, component.prefix, component.stop_floor
            if config.scheduler not in ('weighted', 'focus') or config.root_portfolio != 1:
                raise ContractError('component mode requires the weighted or focus scheduler and a single root')
        elif scope_prefix is not None:
            if config.scheduler != 'weighted' or config.root_portfolio != 1:
                raise ContractError('Diagnostic scope requires indexed weighted scheduling and a single root')
            if canonical(initial_prefix or []) != canonical(scope_prefix):
                raise ContractError('Diagnostic initial prefix must equal scope prefix')
        if config.root_policies and component is None and (initial_prefix or scope_prefix is not None or stop_floor is not None or config.root_portfolio != 1):
            raise ContractError('root policies need a fresh, unscoped start and a single native root')
        self.ctx, self.out, self.pool, self.config = ctx, out, pool, config
        self.initial_prefix, self.scope_prefix, self.progress = initial_prefix, scope_prefix, progress
        self.component = component
        self.plugins = list(plugins)
        if len({plugin.name for plugin in self.plugins}) != len(self.plugins):
            raise ContractError('a plugin can be named only once')
        ev = (evaluator_class(pool, ctx, out, config, advisor) if scope_prefix is None and stop_floor is None else
              evaluator_class(pool, ctx, out, config, advisor, scope_prefix=scope_prefix, stop_floor=stop_floor))
        ev.load_archive(import_checkpoints)
        self.ev = ev
        self.frontier = DiverseFrontier(limit=config.archive_entries)
        # Per-seed gate model: fitted on this run's own native boss fights, used to
        # order options (focus scheduler) and, with config.prior, as rollout tiers.
        probing = bool(config.gate_probe_schedule)
        if probing and (scope_prefix is not None or stop_floor is not None or advisor is None):
            raise ContractError('gate probes need an unscoped search with the combat advisor')
        models = GateModels
        for plugin in self.plugins:
            models = plugin.model_class(models)
        self.models = ((models(config.solver_seed, probes=True) if probing else models(config.solver_seed))
                       if probing or config.prior or config.scheduler == 'focus' else None)
        if config.prior:
            ev.prior_provider = self.models.prior
        self.scheduler = (
            IncumbentScheduler() if config.scheduler == 'incumbent' else
            NativeTreeScheduler(ctx) if config.scheduler == 'tree' else
            DepthFirstScheduler() if config.scheduler == 'dfs' else
            FocusScheduler(scope_prefix, models=self.models, elites=config.focus_elites, pool=config.focus_pool,
                           focus=config.focus_share, site_cap=config.site_cap, cluster_cap=config.focus_cluster_cap,
                           cluster_percent=config.focus_cluster_percent, family=config.focus_family,
                           optimism=config.focus_optimism, carry=config.focus_carry) if config.scheduler == 'focus' else
            IndexedStrategicScheduler(scope_prefix))
        # Request sources besides the scheduler.
        self.repairs = RepairQueue(config.repair_mode)
        self.urgent = deque()
        self.seen_rollouts = set()
        # Mechanisms observe each absorbed result in this order; plugins last.
        self.gate_probes = GateProbes(self) if probing else None
        self.mechanisms = [m for m in (self.gate_probes,) if m is not None]
        self.mechanisms.append(GateRetry(self) if config.repair_mode == 'gate' else CombatProbe(self))
        self.deepen = Deepen(self)
        # Built-in mechanisms retain the frozen i054a Search contract.
        # Optional plugins receive a narrow, capability-only facade.
        self.plugin_context = PluginContext(self)
        for plugin in self.plugins:
            plugin.attach(self.plugin_context)
        self.allocator = PriorityAllocator(default_sources(
            self, [source for plugin in self.plugins for source in plugin.sources(self.plugin_context)]))
        # evaluation label -> root policy of its lineage (only labels that are not native)
        self.families = {}
        self.portfolio = (NATIVE, *config.root_policies)
        self.restarts = 0
        self.lanes = max(1, getattr(pool.resources, 'workers', 1))
        self.scheduled = Counter()
        # Report state.
        self.best = None
        self.best_label = None
        self.certificate = None
        self.groups_report = []
        self.failures = []
        self.branches = set()
        self.rooms = set()
        self.completed = self.terminal = self.fresh = self.act2 = self.act3 = 0
        self.last_saved_records = 0
        self.result = {'schema': 'spire-p5-result/v1', 'context': ctx, 'configuration': config.__dict__,
            'resources': pool.resources.as_dict(), 'native_runtime': getattr(pool, 'runtime', None), 'status': 'UNKNOWN', 'lower_bound': 0, 'upper_bound': 1,
            'optimal_in_backend': False, 'game_equivalence_verified': False,
            'semantic_scope': 'bundled native DLL commands in patched offline TestMode',
            'information_mode': 'full; NOT a first-attempt public-information evaluation',
            'heuristic_pruning': True, 'exclusion_certificates': [], 'evaluations': []}
        if scope_prefix is not None or stop_floor is not None:
            self.result['diagnostic_scope'] = {'prefix_length': len(scope_prefix or []), 'stop_floor': stop_floor, 'counts_as_fresh_planner_run': False}
        if self.plugins:
            self.result['plugins'] = [plugin.describe() for plugin in self.plugins]
        if component is not None:
            self.result['component'] = component.describe()

    # Request sources ------------------------------------------------------

    def macro_spec(self):
        """The strategic scheduler's next lookahead group as a request, or None."""
        group = self.scheduler.next()
        if not group:
            return None
        cfg = self.config
        group['prefix_length'] = len(group['prefix'])
        request = self.ev.request(group.pop('prefix'), policy=group.get('policy', 0),
            horizon=None if cfg.scheduler == 'dfs' else group['floor'] + cfg.lookahead_floors,
            actions=cfg.max_decisions if cfg.scheduler == 'dfs' else
                min(cfg.max_decisions, group['prefix_length'] + cfg.lookahead_actions))
        self.groups_report.append(group)
        return {'kind': 'macro_' + group['category'], 'category': group['category'],
                'group': group, 'family': self.families.get(group.get('source')), 'request': request}

    def restart_spec(self, slot):
        """A fresh rollout; restarts rotate through the root policies (native only without a portfolio)."""
        portfolio = self.portfolio
        spec = {'kind': 'restart', 'category': 'exploration',
                'family': portfolio[self.restarts % len(portfolio)] if self.restarts % len(portfolio) else None,
                'request': self.ev.request(self.scope_prefix or [], policy=self.ev.serial + slot + 1)}
        self.restarts += 1
        return spec

    @property
    def done(self):
        """A verified win, or (component mode) the target gate passed or nothing new left to try."""
        return bool(self.certificate) or (self.component is not None and
                                          (self.component.passed_at is not None or self.component.exhausted_at is not None))

    # Absorption -------------------------------------------------------------

    def absorb(self, items):
        from .strategy import FailureAnalyzer, strategic_trace
        for spec, candidate, label in items:
            if spec['kind'] == 'gate_probe':
                # Synthetic outcomes go to the gate model's probe tables and nowhere else.
                self.gate_probes.absorb_batch(self, spec, candidate)
                if self.progress:
                    self.progress({'label': label, 'kind': 'gate_probe', 'classification': 'SYNTHETIC_PROBE', 'floor': None, 'restored_prefix': 0})
                continue
            classification = classify_failure(candidate)
            obs = candidate.get('observation') or {}
            family = spec.get('family')
            if family:
                self.families[label] = family
            if candidate.get('trace'):
                self.completed += 1
                self.branches.add(strategic_trace(candidate))
                self.rooms.add(strategic_trace(candidate, True))
                self.act2 += int(obs.get('act', 0) >= 1)
                self.act3 += int(obs.get('act', 0) >= 2)
                self.fresh += int(not spec['request']['history'] or spec['kind'] == 'root_rollout')
            self.terminal += int(classification in ('NATIVE_ROUTE_DEATH', 'NATIVE_WIN_CANDIDATE'))
            if self.models is not None:
                # The parent is the trajectory this request deviated from (or
                # re-solved); the pair feeds the difference model.
                self.models.add(candidate, label, (spec.get('group') or spec.get('repair') or {}).get('source'))
            self.frontier.add(candidate, label)
            self.scheduler.add(candidate, label)
            if self.best is None or utility(candidate) > utility(self.best):
                self.best, self.best_label = candidate, label
            if classification == 'NATIVE_ROUTE_DEATH':
                probe = failure_combat_prefix(candidate)
                self.failures.append({'label': label, **FailureAnalyzer.analyze(candidate),
                    'entry_prefix_length': len(probe['prefix']) if probe else None})
            for mechanism in self.mechanisms:
                mechanism.observe(self, spec, candidate, label, classification, family)
            for plugin in self.plugins:
                plugin.observe(self.plugin_context, spec, candidate, label, classification, family)
            passed = (self.component is not None and self.component.passed_at is None
                      and self.component.passed(candidate))
            if passed:
                self.component.record(label, self.ev, self.completed)
            elif self.component is not None and candidate.get('trace'):
                self.component.observe(spec['kind'], candidate)
                if self.component.exhaust(label, self.ev, self.completed, self.lanes, bool(self.repairs or self.urgent)):
                    break
            if is_winning_candidate(candidate):
                self.certificate = self.ev.verify_win(candidate, label)
                if self.certificate:
                    break
            if self.progress:
                self.progress({'label': label, 'kind': spec['kind'], 'classification': classification,
                    'floor': obs.get('floor'), 'restored_prefix': candidate.get('restored_prefix', 0),
                    **({'component_passed': True} if passed else {})})
            if passed:
                break

    # Report -----------------------------------------------------------------

    def save(self, force=False):
        ev, cfg, records = self.ev, self.config, self.ev.records
        if not force and not self.certificate and len(records) - self.last_saved_records < cfg.snapshot_stride:
            return
        self.last_saved_records = len(records)
        metrics = {**self.scheduler.snapshot(), 'scheduled_evaluations': ev.serial, 'proposed_evaluations': sum(self.scheduled.values()), 'inflight_evaluations': ev.pending_count,
            'executed_evaluations': sum(not r.get('cache_hit') for r in records),
            'valid_evaluations': sum(r.get('classification') not in INVALID for r in records),
            'candidates_completed': self.completed, 'complete_games': self.terminal, 'fresh_start_candidates': self.fresh,
            'unique_strategic_routes': len(self.branches), 'unique_room_routes': len(self.rooms),
            'routes_reaching_act2': self.act2, 'routes_reaching_act3': self.act3,
            'scheduled_by_kind': dict(self.scheduled), 'prefix_cache_hits': sum(r.get('checkpoint_prefix', 0) > 0 for r in records),
            'pending_repairs': len(self.repairs), 'executed_deep_boss_repairs': sum(bool((r.get('repair') or {}).get('deep')) for r in records),
            'prefix_cache_misses': sum(not r.get('cache_hit') and r.get('checkpoint_prefix', 0) == 0 for r in records)}
        if cfg.root_policies:
            metrics['evaluations_by_root_policy'] = {name: sum((r.get('family') or NATIVE) == name for r in records if r.get('kind') != 'gate_probe')
                                                     for name in self.portfolio}
        if self.gate_probes is not None:
            metrics['gate_probes'] = self.gate_probes.metrics(self)
        if self.plugins:
            metrics['plugins'] = {plugin.name: plugin.metrics(self.plugin_context) for plugin in self.plugins}
        # The append-only ledger keeps every record. Live snapshots have bounded
        # record history; completion publishes the full report exactly once.
        live_tail = cfg.low_io and not force and len(records) > 64
        self.result.update(evaluations=records[-64:] if live_tail else records,
            evaluations_total=len(records), evaluations_truncated=live_tail, evaluations_ledger='evaluations.jsonl',
            frontier=self.frontier.snapshot(), failures=self.failures[-64:] if live_tail else self.failures, failures_total=len(self.failures),
            lookahead_groups=self.groups_report[-64:] if live_tail else self.groups_report, lookahead_groups_total=len(self.groups_report),
            search_metrics=metrics, cache_hits=ev.cache.hits,
            checkpoints=ev.checkpoints.snapshot(limit=64 if live_tail else None) if ev.checkpoints else {}, pool_stats=self.pool.stats,
            elapsed_seconds=ev.clock() - ev.started, best_label=self.best_label,
            best_observation=(self.best or {}).get('observation'),
            unresolved_regions=[{'kind': 'all runs not excluded by a proof', 'upper_bound': 1}])
        if self.models is not None:
            if force:
                self.models.refit()
            self.result['gate_models'] = self.models.snapshot()
        if self.component is not None:
            self.result['component'] = self.component.describe()
        if self.certificate:
            self.result.update(status='VERIFIED_WIN_IN_NATIVE_HOST', lower_bound=1, optimal_in_backend=True, unresolved_regions=[])
        write_json(self.out / 'result.json', self.result, compact=cfg.low_io)

    # Loop -------------------------------------------------------------------

    def prepare(self, specs):
        """Request-patch hook: every plugin, in order, may edit each request
        before it is counted and dispatched (the cache key covers the edit)."""
        for plugin in self.plugins:
            for spec in specs:
                plugin.patch_request(self.plugin_context, spec)
        return specs

    def run_batch(self, specs):
        self.prepare(specs)
        self.scheduled.update(s['kind'] for s in specs)
        items = self.ev.batch(specs)
        self.absorb(items)
        self.save()
        return items

    def start(self):
        cfg, ev = self.config, self.ev
        if cfg.root_portfolio > 1:
            from .root_portfolio import root_requests
            if self.initial_prefix:
                raise ContractError('root portfolio only supports fresh initial runs')
            request = ev.request([])
            request['stop_at_strategic_decision'] = True
            # A menu-only observation must not outrank dead full campaigns merely
            # because it is alive at floor zero; do not insert it into the frontier.
            self.scheduled['root_menu'] += 1
            initial = ev.batch(self.prepare([{'kind': 'root_menu', 'category': 'exploration', 'request': request}]))
            roots, deferred = root_requests(initial[0][1], ev, min(cfg.root_portfolio, self.lanes)) if initial else ([], 0)
            self.result['root_portfolio'] = {'offered_branches_scheduled': len(roots), 'deferred_actions': deferred,
                'source': 'actual native initial menu', 'prior_routes_loaded': False}
            if roots:
                self.urgent.extend(roots)
            else:
                self.run_batch([{'kind': 'baseline', 'category': 'exploration', 'request': ev.request([])}])
        else:
            # The baseline alone leaves every other worker idle for its whole
            # duration: the extra root policies cost no wall time.
            self.run_batch([{'kind': 'baseline', 'category': 'exploration', 'request': ev.request(self.initial_prefix or [])}] +
                [{'kind': 'root_policy', 'category': 'exploration', 'family': name, 'request': ev.request(self.scope_prefix or [])} for name in cfg.root_policies])
            if cfg.root_policies:
                self.result['root_policies'] = {'portfolio': list(self.portfolio), 'continuation': 'lineage keeps its root policy table',
                    'tables_are': 'diversity sources; allocation only, never legality or a bound'}

    def run(self):
        cfg, ev = self.config, self.ev
        self.start()
        while not self.done:
            can_schedule = ev.serial < cfg.evaluations and ev.clock() < ev.deadline
            window = cfg.dispatch_window if cfg.dispatch_mode == 'ordered' else self.lanes
            free = max(0, window - ev.pending_count)
            specs = self.allocator.fill(self, min(free, cfg.evaluations - ev.serial)) if can_schedule else []
            if specs:
                self.prepare(specs)
                self.scheduled.update(s['kind'] for s in specs)
                ev.dispatch(specs)
            if not ev.pending_count:
                break
            trials = ev.collect(ordered=True) if cfg.dispatch_mode == 'ordered' else ev.collect()
            if cfg.dispatch_mode == 'batch':
                while ev.pending_count:
                    trials.extend(ev.collect())
                trials.sort(key=lambda item: int(item[2].split('-')[1]))
            if not trials:
                continue
            self.absorb(trials)
            self.save()
            if self.done:
                ev.cancel_remaining()
                self.save()
                break
            self.deepen.after_collect(self, trials)
            for plugin in self.plugins:
                plugin.after_collect(self.plugin_context, trials)
        self.result['stop_reason'] = ('verified_boolean_maximum' if self.certificate else
                                      'component_gate_passed' if self.component is not None and self.component.passed_at else
                                      'component_exhausted' if self.component is not None and self.component.exhausted_at else
                                      'time_budget' if ev.clock() >= ev.deadline else 'evaluation_budget')
        self.save(force=True)
        return self.result
