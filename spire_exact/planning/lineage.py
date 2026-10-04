"""Observed campaign lineages and reversible stall allocation.

    A lineage is the exact sequence of real non-combat decisions before act 3,
including act 2 boss rewards. It is not the root policy or a deck cluster.
These records allocate work only: a pause never proves a route impossible,
and unresolved branches stay in their original queues.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import math

from ..canonical import canonical
from .archive import classify_failure
from .gatemodel import boss_fight_rows


def _act(observation):
    value = observation.get('act')
    return value if type(value) is int and value >= 0 else None


def _noncombat(row):
    obs = row.get('observation') or {}
    if row.get('phase') == 'combat':
        return False
    if row.get('phase') != 'select_cards':
        return True
    if obs.get('turn') is not None or obs.get('hand') is not None or obs.get('enemies'):
        return False
    # Missing selection/combat semantics are not silently classified as an
    # out-of-combat choice. Native shop/forge/event menus supply purpose.
    return True if (obs.get('selection') or {}).get('purpose') else None


def _digest(parts):
    # A display identifier only. Equality uses the complete canonical parts.
    return hashlib.sha256(canonical([part.hex() for part in parts])).hexdigest()


@dataclass
class _Lineage:
    decisions: tuple
    first_route: tuple | None
    passed: set = field(default_factory=set)
    gates: dict = field(default_factory=dict)
    gate_baseline: dict = field(default_factory=dict)
    parent_gates: dict = field(default_factory=dict)
    target_act: int | None = None
    trigger: dict | None = None
    active_triggers: list = field(default_factory=list)


@dataclass
class _Observation:
    lineage: tuple | None
    decisions: tuple             # (trace index, canonical decision)
    acts: tuple                  # (index, act) changes, not one slot per combat action
    action_count: int
    terminal_act: int | None
    boss_entries: dict           # act -> first observed native boss action index

    def act_at(self, index):
        if not 0 <= index < self.action_count:
            return None
        act = None
        for start, value in self.acts:
            if start > index:
                break
            act = value
        return act


class LineageBackoff:
    """Use the existing stall threshold for distinct native boss entries.

    New menus, decks and proposals are not progress. A new observed boss pass
    is progress. Unknown/resource/synthetic results supply neither attempts
    nor progress. A previously unseen lineage actually entering act 3, or a
    newly passed boss of a paused lineage, resumes allocation. Clock fields
    stay None when no completion time was supplied.
    """

    def __init__(self, threshold, *, enabled=False, f2=False, prefixes=None):
        if type(f2) is not bool or (f2 and (not enabled or threshold <= 0)):
            raise ValueError('F2 backoff requires extended stall and a positive threshold')
        self.threshold, self.enabled = threshold, enabled
        self.f2 = f2
        self.prefixes = prefixes
        self.labels = {}
        self.lineages = {}
        self.entered = set()
        self.seen_labels = set()
        self.unknown_lineages = 0
        self.events = []
        self.windows = []
        self.next_window = 300.0
        self.last_seconds = None

    @property
    def active(self):
        return self.enabled and self.threshold > 0

    def _in_scope(self, gate):
        return (gate[0] in (1, 2) and gate[1] == 0) or (self.f2 and tuple(gate) == (2, 1))

    @staticmethod
    def _observation(result):
        trace = result.get('trace') or []
        evidence = result.get('decision_evidence') or []
        acts, decisions, first_route, bosses = [], [], [], {}
        complete = len(evidence) >= len(trace)
        for index, (action, row) in enumerate(zip(trace, evidence)):
            obs = row.get('observation') or {}
            act = _act(obs)
            if not acts or acts[-1][1] != act:
                acts.append((index, act))
            if act is None:
                complete = False
            noncombat = _noncombat(row)
            if noncombat is None:
                complete = False
            if noncombat is False and act is not None and obs.get('room') == 'Boss':
                bosses.setdefault(act, index)
            if noncombat is not True or act is None or act >= 2:
                continue
            part = canonical({'act': act, 'floor': obs.get('floor'),
                              'phase': row.get('phase'), 'action': action})
            decisions.append((index, part))
            if act == 0 and row.get('phase') == 'map':
                first_route.append(part)
        terminal_act = _act(result.get('observation') or {})
        entered = terminal_act is not None and terminal_act >= 2 or any(
            act is not None and act >= 2 for _, act in acts)
        # Before act 3 the full *observed* decision prefix still identifies an
        # early source. It is not labelled as an entered act 3 lineage until
        # the native observation actually proves that transition.
        lineage = tuple(part for _, part in decisions) if complete else None
        return _Observation(lineage, tuple(decisions), tuple(acts), len(trace), terminal_act, bosses), (
            tuple(first_route) if first_route else None), entered

    @staticmethod
    def _source(spec):
        if not isinstance(spec, dict):
            return spec if isinstance(spec, str) else None
        for name in ('preparation', 'group', 'repair'):
            if isinstance(spec.get(name), dict) and spec[name].get('source'):
                return spec[name]['source']
        direct = spec.get('memory_prefix_source') or spec.get('source')
        if direct:
            return direct
        if spec.get('kind') in ('paired_card_probe', 'real_card_menu_probe', 'gate_probe'):
            return spec.get('base') if isinstance(spec.get('base'), str) else None
        return None

    def source_lineage(self, source_or_spec):
        observation = self.labels.get(self._source(source_or_spec))
        return observation.lineage if observation is not None else None

    def _spec_act(self, spec):
        if not isinstance(spec, dict):
            return None
        gate = spec.get('gate')
        if isinstance(gate, (tuple, list)) and gate and type(gate[0]) is int:
            return gate[0]
        for field in ('preparation', 'group', 'repair'):
            metadata = spec.get(field)
            if not isinstance(metadata, dict):
                continue
            act = metadata.get('act')
            if type(act) is int:
                return act
            observation = self.labels.get(metadata.get('source'))
            index = metadata.get('index')
            if observation is not None and type(index) is int:
                act = observation.act_at(index)
                if act is not None:
                    return act
        # F1 winner and memory continuation consumers carry full histories
        # rather than menu indices. Their source's final observed act is the
        # conservative allocation stage, not a claim about unexecuted actions.
        source = self.labels.get(self._source(spec))
        return source.terminal_act if source is not None else None

    def preparation_blocked(self, source_or_spec):
        lineage = self.source_lineage(source_or_spec)
        record = self.lineages.get(lineage)
        return bool(self.active and record is not None and record.target_act is not None)

    def dispatch_blocked(self, spec):
        lineage = self.source_lineage(spec)
        record = self.lineages.get(lineage)
        if not self.active or record is None or record.target_act is None:
            return False
        act = self._spec_act(spec)
        return act is not None and act > record.target_act

    def candidate_before_boss(self, branch):
        source = self.labels.get(branch.source_label)
        entry = source.boss_entries.get(branch.act) if source is not None else None
        return entry is not None and branch.index < entry

    def target_act(self):
        targets = [row.target_act for row in self.lineages.values() if row.target_act is not None]
        return min(targets) if self.active and targets else None

    def _resume(self, seconds, reason, new_lineage=None):
        for key, record in self.lineages.items():
            if record.target_act is None:
                continue
            event = {'event': 'lineage_resumed', 'lineage': _digest(key),
                     'completed_wall_seconds': seconds, 'reason': reason,
                     'new_lineage': _digest(new_lineage) if new_lineage is not None else None}
            self.events.append(event)
            if reason == 'new_lineage_entered_act3':
                for trigger in record.active_triggers:
                    trigger['first_new_lineage_act3_seconds'] = seconds
                    trigger['first_new_lineage'] = event['new_lineage']
            record.active_triggers.clear()
            record.target_act = None
            record.parent_gates.clear()
            record.gate_baseline = {gate: set(values[0]) for gate, values in record.gates.items()}

    def _trigger(self, key, record, target, seconds, gate, entries):
        record.target_act = target
        record.parent_gates.clear()
        event = {'event': 'lineage_backoff', 'lineage': _digest(key),
                 'completed_wall_seconds': seconds, 'target_act': target,
                 'target_act_number': target + 1, 'stalled_gate': list(gate),
                 'distinct_entries': len(entries), 'threshold_entries': self.threshold,
                 'first_new_lineage_act3_seconds': None, 'first_new_lineage': None}
        record.trigger = event
        record.active_triggers.append(event)
        self.events.append(event)

    def observe(self, result, label, spec=None, *, completed_wall_seconds=None):
        if label in self.seen_labels:
            return
        self.seen_labels.add(label)
        seconds = completed_wall_seconds if completed_wall_seconds is not None else result.get('completed_wall_seconds')
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
            seconds = None
        if seconds is not None:
            self.last_seconds = seconds if self.last_seconds is None else max(seconds, self.last_seconds)
        if result.get('synthetic') or result.get('cache_hit') or (
            isinstance(spec, dict) and spec.get('cache_hit')):
            self._sample(seconds)
            return
        if classify_failure(result) not in ('NATIVE_ROUTE_DEATH', 'NATIVE_WIN_CANDIDATE',
                                            'DECISION_BOUNDARY', 'SEARCH_BUDGET'):
            self._sample(seconds)
            return
        if not result.get('trace'):
            self._sample(seconds)
            return
        observation, first_route, entered = self._observation(result)
        self.labels[label] = observation
        key = observation.lineage
        known_record = self.lineages.get(key)
        if known_record is not None:
            # Reuse complete decision bytes across thousands of combat-trace
            # variants. Action indices differ; projected equality is used
            # only by this allocator and never by replay/cache/proof code.
            key = observation.lineage = known_record.decisions
            observation.decisions = tuple((index, part) for (index, _), part in
                                          zip(observation.decisions, key))
        new_entry = entered and key is not None and key not in self.entered
        if entered and key is None:
            self.unknown_lineages += 1
        if new_entry:
            self.entered.add(key)
            self._resume(seconds, 'new_lineage_entered_act3', key)
            self.events.append({'event': 'lineage_entered_act3', 'lineage': _digest(key),
                                'first_act_route': _digest(first_route) if first_route else None,
                                'completed_wall_seconds': seconds})
        fights = boss_fight_rows(result)
        path = self.prefixes.index(result['trace']) if self.prefixes is not None and fights else None
        def entry_key(row):
            return path[row['index']] if path is not None else canonical(result['trace'][:row['index']])
        progressed = False
        if key is not None:
            record = self.lineages.setdefault(key, _Lineage(key, first_route))
            passed = {tuple(row['gate']) for row in fights if row['lost'] is None}
            # Replayed passes already in this record are not new progress.
            # F2 participates only under its separate experimental switch.
            passed = {gate for gate in passed if self._in_scope(gate)}
            progressed = bool(passed - record.passed)
            record.passed.update(passed)
            if progressed:
                if record.target_act is not None:
                    self.events.append({'event': 'lineage_resumed', 'lineage': _digest(key),
                                        'completed_wall_seconds': seconds, 'reason': 'new_boss_pass',
                                        'new_lineage': None})
                record.target_act = None
                record.parent_gates.clear()
                record.active_triggers.clear()
                record.gate_baseline = {gate: set(values[0]) for gate, values in record.gates.items()}
            for row in fights:
                gate = tuple(row['gate'])
                if not self._in_scope(gate):
                    continue
                entries, passes = record.gates.setdefault(gate, [set(), set()])
                entry = entry_key(row)
                entries.add(entry)
                if row['lost'] is None:
                    passes.add(entry)
            if self.active and record.target_act is None and record.gates:
                gate = max(record.gates)
                entries, passes = record.gates[gate]
                fresh_entries = entries - record.gate_baseline.get(gate, set())
                if not passes and len(fresh_entries) >= self.threshold:
                    self._trigger(key, record, gate[0] - 1, seconds, gate, fresh_entries)
        # A parent gate is evaluated only by work actually sent back to that
        # act. The old act 2 pass carried in a paused full trace is not an
        # observation that the new parent branches passed their boss.
        # Corridor deaths and repeated exact entries never advance this count.
        source_key = self.source_lineage(spec)
        source_record = self.lineages.get(source_key)
        if (self.active and source_record is not None and source_record.target_act == 1 and
                self._spec_act(spec) == 1 and not progressed and not new_entry):
            for row in fights:
                gate = tuple(row['gate'])
                if gate[0] != 1:
                    continue
                entries, passes = source_record.parent_gates.setdefault(gate, [set(), set()])
                entry = entry_key(row)
                entries.add(entry)
                if row['lost'] is None:
                    passes.add(entry)
            if source_record.parent_gates:
                gate = max(source_record.parent_gates)
                entries, passes = source_record.parent_gates[gate]
                if passes:
                    self.events.append({'event': 'lineage_resumed', 'lineage': _digest(source_key),
                                        'completed_wall_seconds': seconds, 'reason': 'backoff_boss_pass',
                                        'new_lineage': None})
                    source_record.target_act = None
                    source_record.parent_gates.clear()
                    source_record.active_triggers.clear()
                    source_record.gate_baseline = {gate: set(values[0]) for gate, values in source_record.gates.items()}
                elif len(entries) >= self.threshold:
                    self._trigger(source_key, source_record, 0, seconds, gate, entries)
        self._sample(seconds)

    def _sample(self, seconds):
        if seconds is None:
            return
        # A completion at 620 s provides an observation at 620 s, not an
        # invented reconstruction of the state at 300 or 600 s.
        if seconds >= self.next_window:
            self.windows.append({'completed_wall_seconds': seconds,
                                 'interval_end_seconds': self.next_window,
                                 **self._counts()})
            self.next_window = (int(seconds // 300) + 1) * 300.0

    def _counts(self):
        routes = {self.lineages[key].first_route for key in self.entered if key in self.lineages}
        unknown_routes = None in routes
        return {'observed_act3_lineages': len(self.entered),
                'act3_lineages': None if self.unknown_lineages else len(self.entered),
                'unknown_lineage_observations': self.unknown_lineages,
                'observed_first_act_routes': len(routes - {None}),
                'first_act_routes': None if unknown_routes or self.unknown_lineages else len(routes),
                'lineage_definition': 'exact pre-act3 real non-combat decisions, including act2 boss rewards'}

    def candidate_seen(self, branch, action):
        """Did any entering lineage already use this candidate decision stem?

        The comparison omits combat actions, but never equates game states.
        It is an ordering preference only and supplies no deletion reason.
        """
        source = self.labels.get(branch.source_label)
        if source is None:
            return False
        previous = tuple(part for index, part in source.decisions if index < branch.index)
        part = canonical({'act': branch.act, 'floor': branch.floor,
                          'phase': branch.phase, 'action': action})
        stem = previous + (part,)
        return any(key[:len(stem)] == stem for key in self.entered)

    def snapshot(self):
        return {**self._counts(), 'enabled': self.enabled, 'threshold_entries': self.threshold,
                'gate_scope': [[1, 0], [2, 0]] + ([[2, 1]] if self.f2 else []),
                'f2_backoff_enabled': self.f2,
                'completed_wall_seconds': self.last_seconds, 'target_act': self.target_act(),
                'paused_lineages': [{'lineage': _digest(key), 'target_act': row.target_act,
                                      'gates': [{'gate': list(gate), 'entries': len(values[0]),
                                                 'entries_since_resume': len(values[0] - row.gate_baseline.get(gate, set())),
                                                 'passes': len(values[1])}
                                                for gate, values in sorted(row.gates.items())],
                                      'parent_gates': [{'gate': list(gate), 'entries': len(values[0]),
                                                        'passes': len(values[1])}
                                                       for gate, values in sorted(row.parent_gates.items())]}
                                     for key, row in self.lineages.items() if row.target_act is not None],
                'five_minute_observations': list(self.windows), 'events': list(self.events),
                'allocation_only': True, 'paused_branches_remain_unresolved': True}
