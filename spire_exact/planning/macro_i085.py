"""i085 macro allocation: bounded service and structural route experiments.

No objective, native transition, state identity, tactical plan or verification
contract is changed here. Topology describes possible travel, not room rewards
or a bound on any continuation. Shadow candidates do not predict outcomes.
"""
from __future__ import annotations

from collections import Counter, OrderedDict, deque
from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
from time import perf_counter

from ..canonical import canonical, ContractError
from .preparation import NativeMap, PreparationCandidates, coord


class MacroService:
    """Admission-count fairness, NOT a native-seconds budget.

    After `burst` consecutive auxiliary proposals an available, unblocked
    ordinary macro branch is selected first. Root-round ordering is preserved
    by the caller. Failed attempts to find a macro branch consume no queue item.
    """
    def __init__(self, enabled: bool, burst: int):
        if type(enabled) is not bool or type(burst) is not int or burst < 1:
            raise ValueError('invalid macro service settings')
        self.enabled, self.burst = enabled, burst
        self.auxiliary_streak = self.total = self.forced = 0
        self.max_auxiliary_streak = 0
        self.kinds = Counter()

    def due(self) -> bool:
        return self.enabled and self.auxiliary_streak >= self.burst

    def record(self, kind: str, *, core: bool = False, forced: bool = False):
        self.total += 1
        self.kinds[kind] += 1
        if core:
            self.forced += int(forced)
            self.auxiliary_streak = 0
        else:
            self.auxiliary_streak += 1
            self.max_auxiliary_streak = max(self.max_auxiliary_streak, self.auxiliary_streak)

    def snapshot(self):
        return {'enabled': self.enabled, 'auxiliary_burst': self.burst,
                'proposals': self.total, 'forced_macro_opportunities': self.forced,
                'max_auxiliary_streak': self.max_auxiliary_streak, 'by_kind': dict(self.kinds),
                'scope': 'proposed ordinary admissions; not accepted work, seconds, or a completeness guarantee'}


def enumerate_routes(graph: NativeMap, *, max_paths: int, max_expansions: int):
    """Bounded simple-path search, round-robin across legal first moves.

    Depth-first within each first-move arm avoids spending the entire budget
    building shallow paths that never reach a boss. The lexicographic remainder
    is a disclosed bias, not a claim that this is an optimal route enumerator.
    Cyclic topology is allowed; simple proposal paths never repeat a node.
    """
    if any(type(v) is not int or v < 1 for v in (max_paths, max_expansions)):
        raise ValueError('route enumeration budgets must be positive integers')
    stacks = deque([[(start,)] for start in graph.starts]) if graph.boss is not None else deque()
    paths = []
    expanded = 0
    while stacks and len(paths) < max_paths and expanded < max_expansions:
        stack = stacks.popleft()
        path = stack.pop()
        expanded += 1
        if path[-1] == graph.boss:
            paths.append(path)
        else:
            for child in sorted(graph.edges[path[-1]], reverse=True):
                if child not in path:
                    stack.append(path + (child,))
        if stack:
            stacks.append(stack)
    return paths, {'expanded_paths': expanded, 'complete_candidate_paths': len(paths),
                   'enumeration_truncated': bool(stacks), 'missing_boss': graph.boss is None}


def route_distance(graph: NativeMap, left, right) -> Fraction:
    """Three structural differences with no hand-coded room value.

    Ordered native room types, type counts, and coordinates each receive equal
    weight. Equal descriptors NEVER identify game states. Fraction keeps ties
    independent of floating-point rounding and machine scheduling.
    """
    if not left and not right:
        return Fraction(0)
    a = [graph.nodes[p] for p in left]
    b = [graph.nodes[p] for p in right]
    n = max(len(a), len(b), 1)
    sequence = Fraction(sum((a[i] if i < len(a) else None) !=
                            (b[i] if i < len(b) else None) for i in range(n)), n)
    ca, cb = Counter(a), Counter(b)
    counts = Fraction(sum(abs(ca[k] - cb[k]) for k in ca.keys() | cb.keys()), max(1, len(a) + len(b)))
    sa, sb = set(left), set(right)
    coordinates = Fraction(len(sa ^ sb), max(1, len(sa | sb)))
    return (sequence + counts + coordinates) / 3


def representative_routes(graph, paths, anchor=(), *, limit=3):
    """Farthest-first diversification relative to observed travel and peers.

    Paths that differ from a peer by fewer than two node memberships are not
    added to this optional portfolio. All ordinary macro branches remain.
    """
    if type(limit) is not int or limit < 1:
        raise ValueError('positive route limit required')
    anchor = tuple(anchor)
    remaining = sorted(set(tuple(p) for p in paths) - {anchor})
    chosen = []
    while remaining and len(chosen) < limit:
        references = ([anchor] if anchor else []) + chosen
        eligible = [p for p in remaining if all(len(set(p) ^ set(q)) >= 2 for q in chosen)]
        if not eligible:
            break
        # No reference: a reproducible seed, not the "best" route.
        pick = min(eligible, key=lambda p: (-min((route_distance(graph, p, q)
                    for q in references), default=Fraction(0)), p))
        chosen.append(pick)
        remaining.remove(pick)
    return chosen


def observed_travel(result, source, graph):
    path = []
    for action, evidence in zip(result.get('trace', [])[source['index']:],
                                result.get('decision_evidence', [])[source['index']:]):
        if (evidence.get('observation') or {}).get('act') != graph.act:
            break
        if action.get('kind') == 'map':
            try:
                point = coord(action)
            except (ValueError, TypeError, KeyError):
                break
            if point not in graph.nodes or (path and point not in graph.edges[path[-1]]):
                break
            path.append(point)
            if point == graph.boss:
                break
    return tuple(path)


@dataclass
class _Cohort:
    key: bytes
    source: dict
    template: dict
    graph: NativeMap
    paths: deque
    label: str
    family: str | None
    charged_bytes: int


class MacroRoutePortfolio:
    """Optional multi-step travel for Act 1/2; native rollout evaluates it.

    The first valid branching map source per observed act is used, not every
    floor. Complete source/request bytes deduplicate proposal cohorts. Bounded
    retention/eviction is allocation only; every evicted item stays unresolved.
    No new shop policy is forced, even when a selected path passes a shop.
    """
    def __init__(self, config, preparation: PreparationCandidates):
        self.config, self.preparation = config, preparation
        self.mode = config.macro_routes
        self.budget = config.macro_route_queue_mib * 1024**2
        self.pending = deque()
        self.pending_keys = set()
        self.pending_bytes = 0
        self.seen = OrderedDict()
        self.seen_bytes = 0
        self.counts = Counter()
        self.samples = deque(maxlen=32)
        self.last_dispatch = 0
        self.proposal_compute_seconds = 0.0
        self.proposal_compute_calls = 0

    def observe(self, result, label, template, source_request, family=None):
        if self.mode == 'off':
            return
        start = perf_counter()
        try:
            return self._observe(result, label, template, source_request, family)
        finally:
            self.proposal_compute_calls += 1
            self.proposal_compute_seconds += perf_counter() - start

    def _observe(self, result, label, template, source_request, family=None):
        if self.mode == 'off' or result.get('synthetic') or 'map_route_plan' in source_request:
            return
        if not template.get('advisor'):
            return
        try:
            if (canonical(self.preparation.context(template)) != canonical(self.preparation.context(source_request))
                    or canonical(template.get('research_progress')) != canonical(source_request.get('research_progress'))):
                self.counts['source_request_contract_rejections'] += 1
                return
        except (KeyError, ValueError, TypeError, ContractError):
            self.counts['source_request_contract_rejections'] += 1
            return
        used = set()
        for source in result.get('map_decision_sources') or []:
            try:
                graph, _ = self.preparation.validate_source(source, result, template)
                if graph.act not in (0, 1) or graph.act in used or len(graph.starts) < 2:
                    continue
                used.add(graph.act)
                base = self.preparation.template_request(template, source_request)
                base['history'] = []
                key = canonical({'source': source, 'template': base})
                if key in self.seen or key in self.pending_keys:
                    self.counts['exact_cohort_duplicates'] += 1
                    continue
                if len(key) > self.budget:
                    self.counts['oversized_sources_unresolved'] += 1
                    continue
                while self.seen and self.seen_bytes + len(key) > self.budget:
                    previous, _ = self.seen.popitem(last=False)
                    self.seen_bytes -= len(previous)
                    self.counts['seen_keys_evicted'] += 1
                self.seen[key] = None
                self.seen_bytes += len(key)
                paths, audit = enumerate_routes(graph, max_paths=self.config.macro_route_paths,
                                               max_expansions=self.config.macro_route_expansions)
                selected = representative_routes(graph, paths, observed_travel(result, source, graph),
                                                 limit=self.config.macro_route_limit)
                self.counts['sources'] += 1
                self.counts['candidates'] += len(selected)
                self.counts['enumeration_truncations'] += int(audit['enumeration_truncated'])
                self.samples.append({'label': label, 'act': graph.act, 'source_index': source['index'],
                    'paths': [[list(point) for point in path] for path in selected],
                    'native_room_sequences': [[graph.nodes[p] for p in path] for path in selected], **audit})
                if self.mode != 'on' or not selected:
                    continue
                # Charge duplicate canonical/source payload conservatively;
                # this is a serialized-payload limit, NOT a Python RSS limit.
                charge = 2 * len(key) + len(canonical([[list(point) for point in path] for path in selected]))
                if charge > self.budget:
                    self.counts['oversized_sources_unresolved'] += 1
                    continue
                while self.pending and self.pending_bytes + charge > self.budget:
                    dropped = self.pending.popleft()
                    self.pending_keys.discard(dropped.key)
                    self.pending_bytes -= dropped.charged_bytes
                    self.counts['queued_candidates_evicted_unresolved'] += len(dropped.paths)
                self.pending.append(_Cohort(key, deepcopy(source), base, graph, deque(selected), label, family, charge))
                self.pending_keys.add(key)
                self.pending_bytes += charge
            except (KeyError, ValueError, TypeError, ContractError):
                self.counts['invalid_sources'] += 1

    def next(self, scheduler, ordinary_proposals):
        if self.mode != 'on' or ordinary_proposals - self.last_dispatch < self.config.macro_route_every:
            return None
        for _ in range(len(self.pending)):
            cohort = self.pending.popleft()
            block = {'kind': 'macro_route_portfolio', 'preparation': {'source': cohort.label,
                        'act': cohort.graph.act, 'index': cohort.source['index']}}
            blocked = (getattr(scheduler, 'dispatch_blocked', lambda _: False)(block) or
                       getattr(scheduler, 'preparation_blocked', lambda _: False)(block))
            if blocked:
                self.pending.append(cohort)
                continue
            path = cohort.paths.popleft()
            spec = self.preparation.route_spec(cohort.source, cohort.graph, list(path), [],
                    'macro_route_portfolio', cohort.template, cohort.template, cohort.label, cohort.family)
            if cohort.paths:
                self.pending.append(cohort)
            else:
                self.pending_keys.discard(cohort.key)
                self.pending_bytes -= cohort.charged_bytes
            if spec is not None:
                self.last_dispatch = ordinary_proposals
                self.counts['dispatched_proposals'] += 1
                spec['macro_route'] = {'source_index': cohort.source['index'], 'act': cohort.graph.act,
                    'native_room_sequence': [cohort.graph.nodes[p] for p in path], 'structural_only': True}
                return spec
        return None

    def snapshot(self):
        return {'mode': self.mode, 'acts': [0, 1], 'per_source_limit': self.config.macro_route_limit,
                'minimum_proposal_gap': self.config.macro_route_every,
                'pending_cohorts': len(self.pending), 'pending_candidates': sum(len(c.paths) for c in self.pending),
                'charged_pending_bytes': self.pending_bytes, 'seen_key_bytes': self.seen_bytes,
                'serialized_payload_budget': self.budget, 'counts': dict(self.counts),
                'proposal_compute_seconds': self.proposal_compute_seconds,
                'proposal_compute_calls': self.proposal_compute_calls,
                'recent_structural_candidates': list(self.samples),
                'scope': 'optional proposal allocation; shadow has no native outcomes; topology is not state identity'}
