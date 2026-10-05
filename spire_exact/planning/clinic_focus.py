"""i085-final01 focus allocation steered by the gate clinic. Allocation only.

The i082 focus cost (`floors back + 2 x tried - 2 x value`) keeps every focus
pick of a trajectory that died at a final boss inside the final act: each new
death brings fresh, cheap alternatives next to the gate, and the act before is
17+ floors away. i082 escapes only after 32 distinct failed entries per
lineage (count-based stall). Here every elite's picks are split per
(stem, gate) between

* local  - the usual cheapest pending alternative near the fatal gate, and
* reroot - a pending alternative in the act the clinic names (normally the
           act before the gate; one more act back once enough sibling stems
           were diagnosed ROOT), never-tried decision stems first, then the
           learned structural effect toward the diagnosed deficit,

with the reroot share set by the verdict (ROOT high, DEPTH low, UNDECIDED in
between once a stem has enough entries). Nothing is removed or blocked: a
verdict only reorders exact-prefix alternatives that all stay reachable
through the explorer share and later picks.
"""
from __future__ import annotations

from collections import Counter
import json
import math

from .focus import FocusScheduler
from .gate_clinic import GateClinic
from .strategy_priors import LabelEffects, gate_class

DEFAULT_SHARES = {'ROOT': 85, 'UNDECIDED': 34, 'DEPTH': 10}
RESOURCE_PHASES = ('rest', 'map', 'shop')


class ClinicFocusScheduler(FocusScheduler):
    def __init__(self, *args, clinic: GateClinic, root_share: int = DEFAULT_SHARES['ROOT'],
                 undecided_share: int = DEFAULT_SHARES['UNDECIDED'], depth_share: int = DEFAULT_SHARES['DEPTH'],
                 resource_bias: float = 2.0, young_slots: int = 0, repair_hints: bool = False,
                 hint_labels: int = 12, **kwargs):
        super().__init__(*args, **kwargs)
        if not isinstance(clinic, GateClinic):
            raise ValueError('clinic focus needs a GateClinic')
        for value in (root_share, undecided_share, depth_share):
            if type(value) is not int or not 0 <= value <= 100:
                raise ValueError('clinic shares are integer percentages within 0..100')
        if not math.isfinite(resource_bias) or resource_bias < 0:
            raise ValueError('resource bias must be a finite nonnegative number')
        if type(young_slots) is not int or young_slots < 0:
            raise ValueError('young slots must be a nonnegative integer')
        if type(repair_hints) is not bool or type(hint_labels) is not int or hint_labels < 1:
            raise ValueError('invalid repair hint settings')
        if repair_hints and clinic.prior is None:
            raise ValueError('repair hints need the structural prior')
        self.clinic = clinic
        self.effects = LabelEffects()
        clinic.label_effects = self.effects
        self.shares = {'ROOT': root_share / 100.0, 'UNDECIDED': undecided_share / 100.0, 'DEPTH': depth_share / 100.0}
        self.resource_bias = float(resource_bias)
        self.allocation = {}        # (stem, gate) -> [reroot picks, local picks]
        self.paths = {}             # source label -> [(trace index, stem node before that decision)]
        self.modes = Counter()
        self._deficits = {}         # (stem, gate, clinic serial) -> (class, weights, deficits, population)
        self.young_slots = young_slots
        self.young_promotions = 0
        self.repair_hints, self.hint_labels = repair_hints, hint_labels
        self.hinted = 0

    # ---- intake -----------------------------------------------------------
    def add(self, result, label, spec=None, *, completed_wall_seconds=None):
        super().add(result, label, spec, completed_wall_seconds=completed_wall_seconds)
        if result.get('synthetic') or (spec or {}).get('cache_hit'):
            return
        if self.clinic.observe(result, label, spec, self.explorer.prefixes):
            self.effects.observe(result)
            if label in self.sources:
                self.paths[label] = self._stem_path(result)
        if len(self.paths) > 2 * self.pool_size:
            self.paths = {key: value for key, value in self.paths.items() if key in self.sources}

    def _stem_path(self, result):
        """Stem node before every non-combat decision of a pool source."""
        from .lineage import _act, _noncombat
        rows, node = [], 0
        trace = result.get('trace') or []
        for index, (action, row) in enumerate(zip(trace, result.get('decision_evidence') or [])):
            kind = _noncombat(row)
            act = _act(row.get('observation') or {})
            if not kind or act is None:
                continue
            rows.append((index, node))
            part = {'act': act, 'floor': (row.get('observation') or {}).get('floor'),
                    'phase': row.get('phase'), 'action': action}
            node = self.clinic.stems.edges.get((node, _canonical(part)), node)
        return rows

    def record_clinic_readiness(self, label, status, group_key=None):
        return self.clinic.record_readiness(label, status, group_key)

    # ---- verdict helpers --------------------------------------------------
    def verdict_for(self, label):
        key = self.clinic.source(label)
        return (key, self.clinic.verdict(*key)) if key is not None else (None, None)

    def retry_rank(self, spec):
        """0: DEPTH stem first, 1: undecided/unknown, 2: ROOT stem last."""
        label = (spec.get('repair') or {}).get('source')
        _, verdict = self.verdict_for(label) if label else (None, None)
        if verdict is None:
            return 1
        return 0 if verdict.status == 'DEPTH' else 2 if verdict.status == 'ROOT' else 1

    def contended(self):
        return self.clinic.contended()

    def _places(self):
        """Pool order with young stems first (at most `young_slots`; 0 = off, the default).

        A re-rooted child usually differs from its ROOT parent by a card or
        two, so the deck-family cap would keep it out of the elite places
        behind the parent's better-ranked trajectories. A source whose
        final-act stem has fewer than `min_entries` distinct entries at its
        gate takes a place first; nothing leaves the pool.
        """
        labels = super()._places()
        # Only once some stem was diagnosed ROOT (re-rooting is under way);
        # before that the pool order is left exactly as it is.
        if not self.young_slots or not self.clinic.counts.get('verdict_ROOT'):
            return labels
        young = []
        for label in labels:
            key = self.clinic.source(label)
            if key is None or key[1][0] < self.clinic.final_act:
                continue
            record = self.clinic.records.get(key)
            # The source fought its gate (a recorded entry) but the stem is
            # still below the verdict threshold; hallway deaths are not promoted.
            # A stem whose picks keep dying before its gate would stay young
            # forever: promotion stops after 2 x min_entries focus picks.
            spent = sum(self.allocation.get(key, (0, 0)))
            if (record is not None and not record.passes and 0 < len(record.entries) < self.clinic.min_entries
                    and spent < 2 * self.clinic.min_entries):
                young.append(label)
                if len(young) == self.young_slots:
                    break
        if not young:
            return labels
        chosen = set(young)
        self.young_promotions += sum(1 for index, label in enumerate(young) if labels.index(label) != index)
        return young + [label for label in labels if label not in chosen]

    def _share(self, verdict):
        if verdict is None or verdict.status == 'PASSED':
            return 0.0
        if verdict.status == 'UNDECIDED' and verdict.entries < self.clinic.min_entries:
            return 0.0
        return self.shares.get(verdict.status, 0.0)

    def _deficit_info(self, key):
        cached = self._deficits.get(key)
        if cached is not None and cached[0] == self.clinic.serial:
            return cached[1]
        stem, gate = key
        info = None
        prior = self.clinic.prior
        record = self.clinic.records.get(key)
        if prior is not None and record is not None and record.entries:
            cls = gate_class(gate, self.clinic.final_act)
            population = self.clinic.population(cls)
            weights = self.clinic.weights(cls)
            best = max(record.entries.values(), key=lambda e: (e.removed or 0.0, -e.order))
            _, deficits = prior.deficit(best.features, cls, population, weights)
            info = (cls, weights, deficits, population)
        self._deficits[key] = (self.clinic.serial, info)
        return info

    def _policy_hint(self, key):
        """Tiers for the continuation of a re-root: labels whose effect, as
        learned in this run, moves the stem's diagnosed deficit. Labels never
        observed carry no hint. Allocation data only."""
        info = self._deficit_info(key)
        if info is None or not info[2]:
            return None
        cls, weights, deficits, population = info
        scored = []
        for label, (count, delta) in self.effects.rows.items():
            score = self.clinic.prior.option_score(delta, cls, deficits, population, weights)
            if score >= 1.0:
                scored.append((-score, label))
        scored.sort()
        hint = {label: [2 if -negative >= 2.5 else 1] * 3 for negative, label in scored[:self.hint_labels]}
        return hint or None

    def _repair(self, names, info):
        if info is None or not names:
            return 0.0
        cls, weights, deficits, population = info
        score = self.effects.best(names, lambda delta: self.clinic.prior.option_score(
            delta, cls, deficits, population, weights))
        # An option never seen chosen gets a small exploration credit.
        return 0.25 if score is None else score

    # ---- candidates -------------------------------------------------------
    def _rows(self, source, *, acts=None, bias=None):
        best = None
        pending = self.explorer.pending
        for index, floor, act, phase, alternatives in source.sites:
            if acts is not None and act not in acts:
                continue
            options = [(-self._value(names, act), position, key, penalty)
                       for position, (key, names, penalty) in enumerate(alternatives) if pending(key)]
            if not options:
                continue
            options.sort()
            back = max(0, source.death_floor - floor)
            tried = len(alternatives) - len(options)
            negative, position, key, penalty = options[0]
            value = max(-4.0, min(4.0, -negative))
            extra = bias(phase, act) if bias is not None else 0.0
            row = (back + 2 * tried + penalty - 2.0 * value - extra, -index, position, key, index, floor, act, phase)
            if best is None or row < best:
                best = row
        return best

    def _local(self, source, key, verdict):
        gate_act = key[1][0]
        resource = (verdict is not None and verdict.status == 'DEPTH' and verdict.kind in ('RESOURCE', 'MIXED')
                    and self.resource_bias > 0)

        def favour(phase, act):
            return self.resource_bias if act == gate_act and phase in RESOURCE_PHASES else 0.0
        bias = favour if resource else None
        local = self._rows(source, acts={act for act in range(gate_act, gate_act + 3)}, bias=bias)
        return local if local is not None else self._rows(source, bias=bias)

    def _reroot(self, source, key, verdict):
        target = verdict.target_act if verdict is not None and verdict.target_act is not None else \
            self.clinic.target_act(*key)
        if target is None:
            return None
        info = self._deficit_info(key)
        path = dict(self.paths.get(source.label) or ())
        best = None
        for act in range(target, -1, -1):
            for index, floor, site_act, phase, alternatives in source.sites:
                if site_act != act:
                    continue
                pending = [(position, child, names, penalty) for position, (child, names, penalty)
                           in enumerate(alternatives) if self.explorer.pending(child)]
                if not pending:
                    continue
                tried = len(alternatives) - len(pending)
                before = path.get(index)
                for position, child, names, penalty in pending:
                    action = json.loads(self.explorer.prefixes.actions[child])
                    seen = 0
                    if before is not None:
                        part = {'act': site_act, 'floor': floor, 'phase': phase, 'action': action}
                        seen = int(self.clinic.stem_known(before, part))
                    repair = max(-2.0, min(2.0, self._repair(names, info)))
                    value = max(-4.0, min(4.0, self._value(names, site_act)))
                    cost = (3.0 * seen + 2.0 * tried + penalty + 0.25 * max(0, source.death_floor - floor) / 17.0
                            - 1.5 * repair - 1.0 * value)
                    row = (cost, -index, position, child, index, floor, site_act, phase)
                    if best is None or row < best:
                        best = row
            if best is not None:
                return best
        return None

    def _clinic_candidate(self, source):
        key, verdict = self.verdict_for(source.label)
        if key is None:
            row = self._rows(source)
            return (row, 'local', None, None) if row is not None else None
        share = self._share(verdict)
        counters = self.allocation.setdefault(key, [0, 0])
        if share > 0 and counters[0] < share * (counters[0] + counters[1] + 1):
            row = self._reroot(source, key, verdict)
            if row is not None:
                return row, 'reroot', key, verdict
        row = self._local(source, key, verdict)
        if row is not None:
            return row, 'local', key, verdict
        if share > 0:
            row = self._reroot(source, key, verdict)
            if row is not None:
                return row, 'reroot', key, verdict
        return None

    def _focus(self):
        elites = self._elite_sources()
        weights = [1.0 / (rank + 1) for rank in range(len(elites))]
        if self.family:
            weights = [weight * self.models.optimistic(*self._record(source)) for weight, source in zip(weights, elites)]
        total = sum(weights)
        ranked = sorted(range(len(elites)), key=lambda rank: (
            elites[rank].picks - (self.focus_picks + 1) * weights[rank] / total, rank))
        for rank in ranked:
            source = elites[rank]
            choice = self._clinic_candidate(source)
            if choice is None:
                if not any(self.explorer.pending(child) for _, _, _, _, alternatives in source.sites
                           for child, _, _ in alternatives):
                    source.exhausted = True
                continue
            row, mode, key, verdict = choice
            _, _, _, child, index, floor, act, phase = row
            self.explorer.take(child)
            source.picks += 1
            self.focus_picks += 1
            self.modes[mode] += 1
            if key is not None:
                self.allocation.setdefault(key, [0, 0])[0 if mode == 'reroot' else 1] += 1
            group = {'prefix': self.explorer.prefixes.restore(child), 'category': 'focus', 'phase': phase,
                     'floor': floor, 'index': index, 'source': source.label, 'diagnosis': source.diagnosis,
                     'elite_rank': rank, 'act': act,
                     'clinic': {'mode': mode, 'verdict': verdict.status if verdict else None,
                                'kind': verdict.kind if verdict else None,
                                'gate': list(key[1]) if key else None}}
            if mode == 'reroot':
                group['step_back'] = True
                if self.repair_hints and key is not None:
                    hint = self._policy_hint(key)
                    if hint:
                        group['policy_hint'] = hint
                        self.hinted += 1
            return group
        return None

    def snapshot(self):
        report = super().snapshot()
        rows = []
        for (stem, gate), (back, local) in sorted(self.allocation.items(), key=lambda item: -sum(item[1]))[:24]:
            rows.append({'stem': self.clinic._stem_label(stem), 'gate': list(gate), 'reroot_picks': back,
                         'local_picks': local, 'verdict': self.clinic.verdict(stem, gate).status})
        report['gate_clinic'] = {**self.clinic.snapshot(), 'focus_modes': dict(self.modes),
                                 'allocation': rows, 'shares_percent': {k: round(100 * v) for k, v in self.shares.items()},
                                 'label_effects': self.effects.snapshot(),
                                 'young_stem_slots': self.young_slots, 'young_stem_promotions': self.young_promotions,
                                 'repair_hints': self.repair_hints, 'hinted_reroots': self.hinted,
                                 'pruning': False, 'allocation_only': True}
        return report


def _canonical(value):
    from ..canonical import canonical
    return canonical(value)
