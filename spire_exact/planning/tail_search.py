"""Persistent, cost-serviced anti-tail macro search. Allocation, never proof.

Exact parent/action trie nodes identify candidates. Cohorts only share service
and concurrency; they never identify states, cache entries, or exclusions.
New arrivals enter at virtual time, not zero: frontier growth cannot continually
reset the cost clock. Two lanes retain the old focus policy as a control hedge.
"""
from __future__ import annotations
from collections import Counter, OrderedDict, defaultdict, deque
from dataclasses import dataclass, field
import hashlib
import math
import statistics
import time

from ..canonical import canonical
from .archive import classify_failure
from .focus import FocusScheduler, EXCLUDED_KINDS
from .gatemodel import boss_fight_rows
from .strategy import strategic_phase, FailureAnalyzer


def nonnegative(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


class ServiceClock:
    """Hierarchical virtual start times with reversible job reservations.

    A new/reawakened flow starts no earlier than the current virtual floor.
    Corrections can reduce debt, but never the floor. These are service clocks,
    NOT measured CPU times; measured, estimated, and pending work are separate.
    """
    def __init__(self):
        self.tags = {}
        self.floor = 0.0
        self.birth = {}
        self.serial = 0

    def activate(self, keys):
        keys = tuple(keys)
        continuing = [self.tags[k] for k in keys if k in self.tags]
        if continuing:
            self.floor = max(self.floor, min(continuing))
        for key in keys:
            if key not in self.birth:
                self.serial += 1
                self.birth[key] = self.serial
            self.tags[key] = max(self.floor, self.tags.get(key, self.floor))
        if keys:
            self.floor = max(self.floor, min(self.tags[k] for k in keys))
        return keys

    def choose(self, keys):
        keys = self.activate(keys)
        return min(keys, key=lambda key: (self.tags[key], self.birth[key], key)) if keys else None

    def charge(self, key, seconds):
        if not nonnegative(seconds):
            raise ValueError('finite nonnegative service required')
        self.tags[key] = max(self.floor, self.tags.get(key, self.floor)) + seconds

    def correct(self, key, difference):
        if type(difference) not in (int, float) or not math.isfinite(difference):
            raise ValueError('finite correction required')
        self.tags[key] = max(self.floor, self.tags.get(key, self.floor) + difference)


@dataclass
class Site:
    node: int
    index: int
    floor: int
    act: int
    phase: str
    source: str
    cohort: int
    signature: bytes
    diagnosis: dict
    options: dict = field(default_factory=dict)  # exact child -> native labels
    achievement: tuple = ()
    end_floor: int = 0
    launches: int = 0
    pending: int = 0
    charged_bytes: int = 0

    @property
    def cell(self):
        return (self.act, max(0, self.end_floor - self.floor).bit_length())


class TailFocusScheduler(FocusScheduler):
    def __init__(self, *args, tail_mode='on', tail_sites=1024, tail_mib=16,
                 tail_cohort_inflight=2, solver_seed=0, **kwargs):
        super().__init__(*args, **kwargs)
        if tail_mode not in ('off', 'shadow', 'on'):
            raise ValueError('invalid tail mode')
        if any(type(v) is not int or v < 1 for v in (tail_sites, tail_mib, tail_cohort_inflight)):
            raise ValueError('positive tail budgets required')
        self.mode = tail_mode
        self.limit, self.byte_limit = tail_sites, tail_mib * 1024**2
        self.cohort_limit, self.seed = tail_cohort_inflight, solver_seed
        self.sites = OrderedDict()
        self.payload_bytes = 0
        self.lanes, self.acts = ServiceClock(), ServiceClock()
        self.lanes.activate(['base', 'tail'])
        self.bands = defaultdict(ServiceClock)
        self.cohorts = defaultdict(ServiceClock)
        self.site_clocks = defaultdict(ServiceClock)
        self.cohort_pending = Counter()
        self.tickets = {}
        self.serial = 0
        self.costs = deque(maxlen=64)
        self.measured = self.estimated = 0.0
        self.stats = Counter()
        self.events = deque(maxlen=64)
        self.python_seconds = 0.0
        self.first_entry, self.first_pass = {}, {}
        self.max_stage = (-1, -1)
        self.final_act = None

    @property
    def feedback_ready(self):
        return self.final_act is not None and self.max_stage >= (self.final_act,0)

    def estimate(self):
        return statistics.median(self.costs) if self.costs else 1.0

    def _settle(self, spec, result):
        token = ((spec or {}).get('group') or {}).get('tail_ticket')
        ticket = self.tickets.pop(token, None)
        if ticket is None:
            return  # requeues/duplicate delivery cannot double-settle
        reservation, flow_refs, site, cohort = ticket
        if (spec or {}).get('cache_hit'):
            cost = 0.0
            self.stats['cache_settlements'] += 1
        else:
            micros = (result.get('performance') or {}).get('wall_us')
            if nonnegative(micros):
                cost = micros / 1e6
                self.measured += cost
                if cost > 0:
                    self.costs.append(cost)
            else:
                cost = reservation
                self.estimated += cost
                self.stats['unknown_costs'] += 1
        for clock, key in flow_refs:
            clock.correct(key, cost - reservation)
        if site is not None:
            site.pending -= 1
            self.cohort_pending[cohort] -= 1
            if not self.cohort_pending[cohort]: del self.cohort_pending[cohort]
        self.stats['settled'] += 1

    def prepare_submission(self, spec):
        """Requeues are new physical attempts, not duplicate result delivery.

        Never mutate the original group's ticket: it may already be in the
        append-only evidence ledger. Requeue execution inherits its old lane;
        it can bypass the new-tail-only concurrency cap via the urgent queue.
        """
        if self.mode!='on' or not spec.get('requeued'):
            return
        old=spec.get('group') or {}
        if old.get('tail_lane') not in ('base','tail') or old.get('tail_ticket') in self.tickets:
            return
        group=dict(old)
        lane=group['tail_lane'];child=group.get('tail_child')
        node=self.explorer.prefixes.parents[child] if type(child) is int and 0<child<len(self.explorer.prefixes.parents) else None
        site=self.sites.get(node) if lane=='tail' else None
        group['tail_resubmission']=True
        spec['group']=self._reserve(group,lane,site,site.cohort if site else None)
        self.stats['retry_reservations']+=1

    def release_unsubmitted(self, spec):
        """Refund a proposed task the evaluator never accepted; keep it pending."""
        group = spec.get('group') or {}
        token = group.get('tail_ticket')
        ticket = self.tickets.pop(token, None)
        if ticket is None:
            return
        reservation, refs, site, cohort = ticket
        for clock, key in refs:
            clock.correct(key, -reservation)
        if site is not None:
            site.pending -= 1
            self.cohort_pending[cohort] -= 1
            if not self.cohort_pending[cohort]: del self.cohort_pending[cohort]
        node = group.get('tail_child')
        if node is not None and not group.get('tail_resubmission'):
            self.explorer.submitted.discard(node)
        self.stats['unsubmitted_refunds'] += 1

    def _reserve(self, group, lane, site=None, cohort=None):
        cost = self.estimate()
        refs = [(self.lanes, lane)]
        if site is not None:
            act, band = site.cell
            refs += [(self.acts, act), (self.bands[act], band),
                     (self.cohorts[site.cell], cohort),
                     (self.site_clocks[(site.cell, cohort)], site.node)]
            site.launches += 1
            site.pending += 1
            self.cohort_pending[cohort] += 1
        for clock, key in refs:
            clock.charge(key, cost)
        self.serial += 1
        self.tickets[self.serial] = (cost, refs, site, cohort)
        group['tail_ticket'] = self.serial
        group['tail_lane'] = lane
        self.stats['proposed_' + lane] += 1
        return group

    def _victim_site(self, incoming_act, incoming_cell):
        # Retention is balanced across Acts/scales too. A FIFO archive otherwise
        # undoes multiscale scheduling by evicting all early roots in a late flood.
        eligible = [s for s in self.sites.values() if not s.pending]
        if not eligible:
            return None
        exhausted = [s for s in eligible if not any(self.explorer.pending(k) for k in s.options)]
        if exhausted:
            return exhausted[0].node
        acts = Counter(s.act for s in self.sites.values()); acts[incoming_act] += 1
        act = max({s.act for s in eligible}, key=lambda a: (acts[a], a))
        bands = Counter(s.cell for s in self.sites.values() if s.act == act)
        if incoming_act == act: bands[incoming_cell] += 1
        cell = max({s.cell for s in eligible if s.act == act}, key=lambda c: (bands[c], c))
        candidates = [s for s in eligible if s.cell == cell]
        return max(candidates, key=lambda s: (s.launches, -s.node)).node

    def _observe(self, result, label, spec, wall):
        if result.get('synthetic') or (spec or {}).get('cache_hit'):
            return
        classification = classify_failure(result)
        if classification not in ('NATIVE_ROUTE_DEATH', 'NATIVE_WIN_CANDIDATE', 'SEARCH_BUDGET', 'DECISION_BOUNDARY'):
            return
        trace = result.get('trace') or []
        evidence = (result.get('decision_evidence') or [])[:len(trace)]
        campaign=result.get('campaign') or {}
        acts=campaign.get('act_count')
        if type(acts) is int and acts>0 and campaign.get('final_act_boss_count')==2:
            self.final_act=acts-1
        rows = boss_fight_rows(result)
        passed = [tuple(r['gate']) for r in rows if r['lost'] is None]
        # Timers are observation times, not unobserved in-rollout gate times.
        if nonnegative(wall):
            for row in rows:
                gate = str(tuple(row['gate']))
                self.first_entry.setdefault(gate, wall)
                if row['lost'] is None:
                    self.first_pass.setdefault(gate, wall)
        if passed:
            self.max_stage = max(self.max_stage, max(passed))
        obs = result.get('observation') or {}
        end_floor = int(obs.get('floor') or 0)
        achievement = (int(classification == 'NATIVE_WIN_CANDIDATE'),
                       max(passed, default=(-1, -1)), int(obs.get('act') or 0), end_floor)
        path = self.explorer.prefixes.index(trace)
        # A separate exact strategic-action trie defines an allocation cohort.
        # Using the complete pre-current-Act strategic stem pools tactical aliases
        # but NEVER replaces the actual action prefix for execution or identity.
        if not hasattr(self, '_stem_trie'):
            from .indexed_strategy import PrefixTrie
            self._stem_trie = PrefixTrie()
        stem, last_act, act_stem = 0, None, 0
        diagnosis = FailureAnalyzer.analyze(result)
        for i, row in enumerate(evidence):
            if not strategic_phase(row):
                continue
            observation = row.get('observation') or {}
            act = int(observation.get('act') or 0)
            floor = int(observation.get('floor') or 0)
            if act != last_act:
                act_stem, last_act = stem, act
            stem = self._stem_trie.child(stem, trace[i])
            if i < self.scope_length:
                continue
            node = path[i]
            options = {}
            labels = row.get('option_labels') or []
            for pos, action in enumerate(row.get('available_actions') or []):
                if action.get('kind') in EXCLUDED_KINDS:
                    continue
                child = self.explorer.prefixes.child(node, action)
                if self.explorer.pending(child):
                    options[child] = tuple(labels[pos]) if pos < len(labels) else ()
            if not options:
                continue
            # Full observation+menu guard; mismatches remain in the baseline,
            # not conflated into a new exact-state equality assertion.
            signature = canonical([observation, row.get('available_actions')])
            old = self.sites.get(node)
            if old is not None:
                if signature != old.signature:
                    self.stats['source_guard_mismatch'] += 1
                    continue
                if achievement > old.achievement:
                    old.achievement, old.end_floor = achievement, end_floor
                    old.source, old.diagnosis = label, diagnosis
                continue
            size = len(signature) + sum(len(self.explorer.prefixes.actions[k]) +
                                       sum(len(s.encode()) for s in names) for k, names in options.items())
            if size > self.byte_limit:
                self.stats['oversized_source'] += 1
                continue
            while self.sites and (len(self.sites) >= self.limit or self.payload_bytes + size > self.byte_limit):
                victim = self._victim_site(act, (act, max(0, end_floor-floor).bit_length()))
                if victim is None:
                    break
                gone = self.sites.pop(victim)
                self.payload_bytes -= gone.charged_bytes
                for clock in self.site_clocks.values():
                    clock.tags.pop(victim, None)
                    clock.birth.pop(victim, None)
                self.stats['site_evictions'] += 1
            if len(self.sites) >= self.limit or self.payload_bytes + size > self.byte_limit:
                self.stats['admission_deferred'] += 1
                continue
            self.sites[node] = Site(node, i, floor, act, row['phase'], label, act_stem,
                                   signature, diagnosis, options, achievement, end_floor,
                                   charged_bytes=size)
            self.payload_bytes += size
            self.stats['sites_admitted'] += 1

    def add(self, result, label, spec=None, *, completed_wall_seconds=None):
        start = time.perf_counter()
        try:
            self._settle(spec, result)
            if result.get('synthetic'):
                self.stats['synthetic_rejected'] += 1
                return
            super().add(result, label, spec, completed_wall_seconds=completed_wall_seconds)
            if self.mode != "off":
                self._observe(result, label, spec, completed_wall_seconds)
                self._compact_clocks()
        finally:
            self.python_seconds += time.perf_counter() - start

    def _compact_clocks(self):
        # Keep service metadata for retained sites and in-flight reservations.
        # Retired flows re-enter at their parent's virtual floor, not zero.
        live=defaultdict(set)
        for site in self.sites.values():
            live[(site.cell,site.cohort)].add(site.node)
        owners={id(clock):owner for owner,clock in self.site_clocks.items()}
        for _,refs,site,_ in self.tickets.values():
            if site is not None:
                for clock,key in refs:
                    owner=owners.get(id(clock))
                    if owner is not None:live[owner].add(key)
        for owner in list(self.site_clocks):
            clock=self.site_clocks[owner];keys=live.get(owner,set())
            for key in set(clock.tags)-keys:
                clock.tags.pop(key,None);clock.birth.pop(key,None)
            if not keys:del self.site_clocks[owner]
        for cell,clock in self.cohorts.items():
            keys={cohort for (which,cohort),nodes in live.items() if which==cell and nodes}
            for key in set(clock.tags)-keys:
                clock.tags.pop(key,None);clock.birth.pop(key,None)
    def _candidates(self):
        cells = defaultdict(lambda: defaultdict(list))
        for site in self.sites.values():
            if site.pending or self.cohort_pending[site.cohort] >= self.cohort_limit:
                continue
            group = {'source': site.source, 'act': site.act, 'index': site.index}
            if self.dispatch_blocked({'group': group}):
                continue
            pending = [k for k in site.options if self.explorer.pending(k)]
            if pending:
                cells[site.cell][site.cohort].append((site, pending))
        return cells

    def _tail_pick(self, cells, consume=True):
        if not cells:
            return None
        # Shadow is a read-only structural suggestion, not an exact alternate
        # scheduler simulation. It never consumes branches or changes clocks.
        if not consume:
            act = min({c[0] for c in cells}, key=lambda k: (max(self.acts.floor, self.acts.tags.get(k, 0)), k))
            band = min(c[1] for c in cells if c[0] == act)
            cohort = min(cells[(act, band)])
            site, pending = min(cells[(act, band)][cohort], key=lambda p: p[0].node)
        else:
            act = self.acts.choose(sorted({c[0] for c in cells}))
            band = self.bands[act].choose(sorted(c[1] for c in cells if c[0] == act))
            cohort = self.cohorts[(act, band)].choose(sorted(cells[(act, band)]))
            pool = cells[(act, band)][cohort]
            key = self.site_clocks[((act, band), cohort)].choose(sorted(s.node for s, _ in pool))
            site, pending = next(p for p in pool if p[0].node == key)
        # Half of site visits deliberately ignore the scalar gate model. This
        # prevents a miscalibrated common model controlling every worker arm.
        model_free = site.launches % 2 == 0
        def key(child):
            data = self.explorer.prefixes.actions[child]
            tie = hashlib.sha256(str(self.seed).encode() + b':' + str(site.node).encode() + data).digest()
            value = 0.0 if model_free else self._value(site.options[child], site.act)
            if not nonnegative(abs(value)):
                value = 0.0
            return (-value, tie, child)
        child = min(pending, key=key)
        group = {'prefix': self.explorer.prefixes.restore(child), 'category': 'tail',
                 'phase': site.phase, 'floor': site.floor, 'act': site.act,
                 'index': site.index, 'source': site.source, 'diagnosis': site.diagnosis,
                 'tail_child': child, 'tail_cell': [act, band], 'tail_cohort': cohort,
                 'tail_model_free': model_free, 'tail_achievement': list(site.achievement)}
        if consume:
            self.explorer.take(child)
            self._reserve(group, 'tail', site, cohort)
        return group

    def next(self):
        if self.mode == 'off':
            return super().next()
        start = time.perf_counter()
        try:
            cells = self._candidates()
            if self.mode == 'shadow':
                candidate = self._tail_pick(cells, consume=False)
                actual = super().next()
                self.stats['shadow_checks'] += 1
                self.stats['shadow_differences'] += int(bool(candidate and actual and
                    canonical(candidate['prefix']) != canonical(actual['prefix'])))
                return actual
            lane = self.lanes.choose(['base', 'tail'] if cells else ['base'])
            if lane == 'tail':
                return self._tail_pick(cells)
            group = super().next()
            if group is not None:
                return self._reserve(group, 'base')
            return self._tail_pick(cells)
        finally:
            self.python_seconds += time.perf_counter() - start

    def snapshot(self):
        base = super().snapshot()
        base['tail_search'] = {'mode': self.mode, 'counts': dict(self.stats),
            'live_sites': len(self.sites), 'serialized_payload_bytes': self.payload_bytes,
            'cohort_inflight_limit': self.cohort_limit,
            'pending_tickets': len(self.tickets), 'pending_reserved_seconds': sum(t[0] for t in self.tickets.values()),
            'known_native_seconds': self.measured, 'unknown_estimated_seconds': self.estimated,
            'current_job_estimate_seconds': self.estimate(), 'python_seconds': self.python_seconds,
            'lane_virtual_tags': dict(self.lanes.tags), 'act_virtual_tags': dict(self.acts.tags),
            'feedback_ready':self.feedback_ready,'final_act':self.final_act,
            'first_entry_observed_seconds': self.first_entry, 'first_pass_observed_seconds': self.first_pass,
            'scope': 'core macro service only; clocks include estimates, not iid hazard or wall-time guarantees',
            'proof_changed': False}
        return base
