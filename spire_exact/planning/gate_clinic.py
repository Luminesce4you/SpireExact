"""Root-versus-depth diagnosis of stalled boss gates. Allocation only.

For every boss gate a trajectory fights, the *stem* is the exact sequence of
real non-combat decisions taken before that gate's act began (for the final
act: i082's lineage, including the act 2 boss reward). The clinic collects the
distinct real entries (exact prefix trie nodes) of each (stem, gate) and asks:

* ROOT  - is the deck the stem fixed unlikely to pass however the act is
          replayed? (best progress saturated far below a pass, an upper
          endpoint estimate below a pass, stronger tactical plans do not lift
          the outcome, full-HP F2 samples all lost, structural deficit);
* DEPTH - is the stem close and still improving? (near miss, retries lift the
          outcome, viable at full HP, outcome rises with entry HP).

The verdict only changes which existing alternatives are tried first and how
work is shared; nothing is removed, blocked or proven. Synthetic readiness
samples are read as allocation evidence and never become real entries. A wrong
verdict costs time, never completeness: every branch stays reachable.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import hashlib
import math

from ..canonical import canonical
from .archive import classify_failure
from .gatemodel import boss_fight_rows
from .indexed_strategy import PrefixTrie
from .lineage import _act, _noncombat
from .strategy_priors import StructuralPrior, features as structural_features, gate_class

VALID = ('NATIVE_ROUTE_DEATH', 'NATIVE_WIN_CANDIDATE', 'SEARCH_BUDGET', 'DECISION_BOUNDARY')
VERDICTS = ('PASSED', 'UNDECIDED', 'DEPTH', 'ROOT')


@dataclass
class _Entry:
    order: int
    source: str
    hp: float | None = None
    features: dict = field(default_factory=dict)
    removed: float | None = None      # best enemy HP removed by the in-rollout plan (loss)
    passed: bool = False
    hp_after: float | None = None
    retry: dict = field(default_factory=dict)   # level -> removed, or None for a pass


@dataclass
class _Record:
    stem: int
    gate: tuple
    entries: dict = field(default_factory=dict)
    passes: int = 0
    orders: int = 0
    verdict: str = 'UNDECIDED'
    changes: int = 0


@dataclass(frozen=True)
class Verdict:
    status: str
    kind: str | None
    entries: int
    best: float | None
    root: int
    depth: int
    evidence: tuple
    target_act: int | None = None


class GateClinic:
    def __init__(self, *, min_entries: int = 6, near: float = 0.85, far: float = 0.55,
                 endpoint_factor: float = 4.0, escalate_after: int = 3, prior: StructuralPrior | None = None,
                 final_act: int = 2):
        if type(min_entries) is not int or min_entries < 3:
            raise ValueError('clinic needs at least three entries per verdict')
        if not 0 < far < near <= 1 or endpoint_factor <= 0 or type(escalate_after) is not int or escalate_after < 1:
            raise ValueError('invalid clinic thresholds')
        self.min_entries, self.near, self.far = min_entries, near, far
        self.endpoint_factor, self.escalate_after = endpoint_factor, escalate_after
        self.prior = prior
        self.final_act = final_act
        self.stems = PrefixTrie()            # exact canonical decision parts, never digests
        self.records = {}                    # (stem node, gate) -> _Record
        self.totals = {}                     # gate -> largest observed enemy life total in a lost fight
        self.parents = {}                    # stem at act a -> stem at act a-1 (same trajectory)
        self.labels = {}                     # source label -> {'stems': {act: node}, 'gate': gate, 'act': act}
        self.readiness = {}                  # final-act stem -> {sample group: status}
        self.label_effects = None            # set by the scheduler (LabelEffects)
        self.events = []
        self.counts = Counter()
        self.serial = 0
        self.version = 0                     # bumps on every absorbed result or readiness change
        self._memo = {}                      # cached verdicts / weights / populations for one version

    def _stem_label(self, node):
        """Display digest of the complete stem path; equality never uses it."""
        parts = []
        while node:
            parts.append(self.stems.actions[node])
            node = self.stems.parents[node]
        return hashlib.sha256(b'\n'.join(reversed(parts))).hexdigest()[:16]

    # ---- intake -----------------------------------------------------------
    def _stems(self, result):
        """{act: stem node} where node = all non-combat decisions before act."""
        trace = result.get('trace') or []
        evidence = (result.get('decision_evidence') or [])[:len(trace)]
        if len(evidence) < len(trace):
            return None
        boundaries, node, last = {}, 0, None
        for action, row in zip(trace, evidence):
            act = _act(row.get('observation') or {})
            if act is None:
                return None
            if last is not None and act < last:
                return None
            for missing in range(0 if last is None else last + 1, act + 1):
                boundaries.setdefault(missing, node)
            last = act
            kind = _noncombat(row)
            if kind is None:
                return None
            if kind:
                part = {'act': act, 'floor': (row.get('observation') or {}).get('floor'),
                        'phase': row.get('phase'), 'action': action}
                node = self.stems.child(node, part)
        if last is not None:
            boundaries.setdefault(last + 1, node)
        return boundaries

    def observe(self, result, label, spec=None, prefixes=None):
        """Absorb one real result. Synthetic, cached and invalid results are ignored."""
        spec = spec or {}
        if result.get('synthetic') or spec.get('cache_hit') or classify_failure(result) not in VALID:
            self.counts['ignored'] += 1
            return False
        trace = result.get('trace') or []
        if not trace:
            return False
        stems = self._stems(result)
        if stems is None:
            self.counts['incomplete_stem'] += 1
            return False
        self.serial += 1
        self.version += 1
        for act in sorted(stems):
            if act - 1 in stems and act >= 1:
                self.parents.setdefault(stems[act], stems[act - 1])
        rows = boss_fight_rows(result)
        path = (prefixes.index(trace) if prefixes is not None else None)
        retried = len(((spec.get('request') or {}).get('history')) or []) if spec.get('kind') == 'gate_retry' else None
        level = int((spec.get('repair') or {}).get('level') or 0) if retried is not None else 0
        for row in rows:
            gate = tuple(row['gate'])
            act = gate[0]
            if act not in stems:
                continue
            node = path[row['index']] if path is not None else canonical(trace[:row['index']])
            record = self.records.get((stems[act], gate))
            if record is None:
                record = self.records[(stems[act], gate)] = _Record(stems[act], gate)
            entry = record.entries.get(node)
            if entry is None:
                record.orders += 1
                entry = record.entries[node] = _Entry(record.orders, label)
                observation = row['entry'] or {}
                entry.features = structural_features(observation)
                entry.hp = entry.features.get('hp_fraction')
            here = level if retried is not None and row['index'] == retried else 0
            if row['lost'] is None:
                record.passes += 1
                after = row['outcome'] - 1.0 if row['outcome'] >= 1.0 else None
                if here:
                    entry.retry[here] = None
                else:
                    entry.passed = True
                    entry.hp_after = after if entry.hp_after is None else max(entry.hp_after, after or 0.0)
            else:
                removed, total = float(row['lost'][0]), float(row['lost'][1])
                self.totals[gate] = max(self.totals.get(gate, 0.0), total)
                if here:
                    if here not in entry.retry or entry.retry[here] is not None:
                        entry.retry[here] = max(entry.retry.get(here) or 0.0, removed)
                else:
                    entry.removed = removed if entry.removed is None else max(entry.removed, removed)
        # Fatal gate of the source (next boss of the act of death when the
        # death is not a boss fight): the verdict that governs its focus picks.
        observation = result.get('observation') or {}
        dead_act = _act(observation)
        gate = None
        if classify_failure(result) == 'NATIVE_ROUTE_DEATH' and dead_act is not None:
            passed = sum(1 for row in rows if row['gate'][0] == dead_act and row['lost'] is None)
            gate = (dead_act, passed)
        self.labels[label] = {'stems': dict(stems), 'gate': gate, 'act': dead_act}
        return True

    def record_readiness(self, label, status, group_key=None):
        """Synthetic full-HP F2 readiness of a source's F1 entry; allocation evidence only."""
        if status not in ('VIABLE', 'PENDING', 'UNKNOWN', 'DEAD_AT_FULL_HP'):
            return False
        info = self.labels.get(label)
        if info is None or self.final_act not in info['stems']:
            return False
        groups = self.readiness.setdefault(info['stems'][self.final_act], {})
        key = group_key if group_key is not None else label
        if groups.get(key) != status:
            groups[key] = status
            self.version += 1
        return True

    def _cached(self, key, compute):
        if self._memo.get('version') != self.version:
            self._memo = {'version': self.version}
        if key not in self._memo:
            self._memo[key] = compute()
        return self._memo[key]

    # ---- queries ----------------------------------------------------------
    def source(self, label):
        info = self.labels.get(label)
        if info is None or info['gate'] is None:
            return None
        stem = info['stems'].get(info['gate'][0])
        return None if stem is None else (stem, info['gate'])

    def _y(self, gate, removed):
        total = self.totals.get(gate, 0.0)
        return max(0.0, min(1.0, removed / total)) if total > 0 else 0.0

    def population(self, cls):
        return self._cached(('population', cls), lambda: [
            entry.features for (stem, gate), record in self.records.items()
            if gate_class(gate, self.final_act) == cls for entry in record.entries.values() if entry.features])

    def _outcomes(self, cls):
        rows = []
        for (_, gate), record in self.records.items():
            if gate_class(gate, self.final_act) != cls:
                continue
            for entry in record.entries.values():
                if not entry.features:
                    continue
                if entry.passed or None in entry.retry.values():
                    rows.append((entry.features, 1.0 + (entry.hp_after or 0.0)))
                elif entry.removed is not None:
                    rows.append((entry.features, self._y(gate, entry.removed)))
        return rows

    def weights(self, cls):
        if self.prior is None:
            return {}
        return self._cached(('weights', cls), lambda: self.prior.posterior(cls, self._outcomes(cls)))

    def verdict(self, stem, gate) -> Verdict:
        return self._cached(('verdict', stem, gate), lambda: self._verdict(stem, gate))

    def _verdict(self, stem, gate) -> Verdict:
        record = self.records.get((stem, gate))
        if record is None:
            return Verdict('UNDECIDED', None, 0, None, 0, 0, ('no_entries',), self.target_act(stem, gate))
        if record.passes:
            return Verdict('PASSED', None, len(record.entries), 1.0, 0, 0, ('gate_passed',), None)
        known = sorted((e for e in record.entries.values() if e.removed is not None or e.retry),
                       key=lambda e: e.order)
        n = len(known)
        if n < self.min_entries:
            return self._log(record, Verdict('UNDECIDED', None, n, None, 0, 0, ('insufficient_entries',),
                                             self.target_act(stem, gate)))
        base = [self._y(gate, e.removed) if e.removed is not None else 0.0 for e in known]
        best_per = []
        lifts = []
        for e, y in zip(known, base):
            retry = [1.0 if v is None else self._y(gate, v) for v in e.retry.values()]
            best_per.append(max([y] + retry))
            if retry:
                lifts.append(max(retry) - y)
        ordered = sorted(best_per)
        best, second = ordered[-1], ordered[-2] if n > 1 else 0.0
        record_index = 0
        for index, y in enumerate(best_per):
            if y >= best - 0.02:
                record_index = index
                break
        since = n - 1 - record_index
        bound = best + self.endpoint_factor * (best - second)
        root, depth, evidence = 0, 0, []

        def note(kind, name, weight=1):
            nonlocal root, depth
            if kind == 'root':
                root += weight
            else:
                depth += weight
            evidence.append(kind + ':' + name)

        if since >= max(self.min_entries, n // 2) and best < self.near:
            note('root', 'saturated')
        if bound < 1.0:
            note('root', 'endpoint_below_pass')
        if best < self.far and n >= 2 * self.min_entries:
            note('root', 'far_from_pass')
        if len(lifts) >= 2 and sum(lifts) / len(lifts) <= 0.03 and best < self.near:
            note('root', 'tactically_insensitive')
        final = gate[0] == self.final_act
        groups = self.readiness.get(stem, {}) if final else {}
        dead = sum(status == 'DEAD_AT_FULL_HP' for status in groups.values())
        viable = sum(status == 'VIABLE' for status in groups.values())
        if final and dead and not viable:
            note('root', 'full_hp_f2_dead', 2)
        deficit = None
        if self.prior is not None:
            cls = gate_class(gate, self.final_act)
            weights = self.weights(cls)
            top = max(zip(best_per, known), key=lambda pair: (pair[0], -pair[1].order))[1]
            deficit, _ = self.prior.deficit(top.features, cls, self.population(cls), weights)
            if deficit >= 0.5:
                note('root', 'structural_deficit')
        if best >= self.near:
            note('depth', 'near_miss', 2)
        if lifts and max(lifts) >= 0.08:
            note('depth', 'retry_lift')
        if since < max(3, n // 4) and bound >= 1.0:
            note('depth', 'improving')
        if final and gate[1] >= 1 and viable:
            note('depth', 'viable_at_full_hp', 2)
        # Would a full-HP entry reach a pass? The linear fit of progress on
        # entry HP is extrapolated to HP share 1; it separates "more HP helps
        # and suffices" (resource depth) from "more HP helps a little but the
        # deck still falls short" (root).
        full = self._full_hp_projection(known, base)
        if full is not None:
            if full >= 0.97:
                note('depth', 'hp_sensitive')
            elif full < self.near and best < self.near:
                note('root', 'full_hp_projection_short')
        # Hysteresis: entering ROOT needs a two-point margin, staying needs one.
        keep_root = record.verdict == 'ROOT' and root >= depth + 1
        if root >= depth + 2 or keep_root:
            status, kind = 'ROOT', None
        elif depth >= 2 and depth >= root + 1:
            resource = any(e.endswith(('viable_at_full_hp', 'hp_sensitive')) for e in evidence if e.startswith('depth:'))
            tactical = any(e.endswith(('near_miss', 'retry_lift')) for e in evidence)
            status, kind = 'DEPTH', ('RESOURCE' if resource and not tactical else 'TACTICAL' if tactical and not resource
                                     else 'MIXED')
        else:
            status, kind = 'UNDECIDED', None
        verdict = Verdict(status, kind, n, round(best, 4), root, depth, tuple(evidence),
                          self.target_act(stem, gate) if status != 'DEPTH' else None)
        return self._log(record, verdict, deficit)

    def _full_hp_projection(self, known, base):
        """Progress projected at a full-HP entry from this stem's own entries,
        or None without enough spread. Losses only; a heuristic, never a bound."""
        pairs = [(e.hp, y) for e, y in zip(known, base) if e.hp is not None]
        if len(pairs) < self.min_entries:
            return None
        xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        sxx = sum((x - mx) ** 2 for x in xs)
        syy = sum((y - my) ** 2 for y in ys)
        if sxx < 1e-6 or syy < 1e-9 or math.sqrt(sxx / len(xs)) < 0.05:
            return None
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        if sxy / math.sqrt(sxx * syy) < 0.35:
            return None
        return my + sxy / sxx * (1.0 - mx)

    def _log(self, record, verdict, deficit=None):
        if verdict.status != record.verdict:
            record.changes += 1
            self.counts['verdict_' + verdict.status] += 1
            if len(self.events) < 256:
                self.events.append({'serial': self.serial, 'stem': self._stem_label(record.stem),
                                    'gate': list(record.gate), 'from': record.verdict, 'to': verdict.status,
                                    'kind': verdict.kind, 'entries': verdict.entries, 'best': verdict.best,
                                    'evidence': list(verdict.evidence),
                                    **({'structural_deficit': round(deficit, 3)} if deficit is not None else {})})
            record.verdict = verdict.status
        return verdict

    def target_act(self, stem, gate):
        """Act to re-root in: the act before the gate's act, or one act
        earlier once enough sibling stems under the same parent were ROOT."""
        act = gate[0]
        if act < 1:
            return None
        parent = self.parents.get(stem)
        if act >= 2 and parent is not None:
            # Sibling stems: other decision sequences of the act before, under
            # the same earlier decisions, that reached this gate. When enough
            # of them stay below a near miss without a pass (or were judged
            # ROOT), changing the act before is not moving the outcome.
            siblings = 0
            for (other, g), r in self.records.items():
                if g != tuple(gate) or self.parents.get(other) != parent:
                    continue
                if r.passes:
                    return act - 1
                if r.verdict == 'ROOT':
                    siblings += 1
                elif len(r.entries) >= 3 and self._best(other, g) < self.near:
                    siblings += 1
            if siblings >= self.escalate_after:
                return act - 2
        return act - 1

    def _best(self, stem, gate):
        record = self.records.get((stem, gate))
        values = []
        for e in (record.entries.values() if record else ()):
            if e.removed is not None:
                values.append(self._y(gate, e.removed))
            values.extend(1.0 if v is None else self._y(gate, v) for v in e.retry.values())
        return max(values, default=0.0)

    def stem_known(self, parent_stem, part) -> bool:
        """Has any absorbed trajectory continued this exact stem with `part`?"""
        return (parent_stem, canonical(part)) in self.stems.edges

    def contended(self) -> bool:
        """The furthest gate reached has min_entries distinct entries and no pass anywhere."""
        gates = {}
        for (_, gate), record in self.records.items():
            row = gates.setdefault(gate, [0, 0])
            row[0] += len(record.entries)
            row[1] += record.passes
        if not gates:
            return False
        furthest = max(gates)
        entries, passes = gates[furthest]
        return not passes and entries >= self.min_entries

    def snapshot(self, limit: int = 24) -> dict:
        rows = []
        for (stem, gate), record in sorted(self.records.items(), key=lambda item: -len(item[1].entries))[:limit]:
            verdict = self.verdict(stem, gate)
            rows.append({'stem': self._stem_label(stem), 'gate': list(gate),
                         'distinct_entries': len(record.entries), 'passes': record.passes,
                         'verdict': verdict.status, 'kind': verdict.kind, 'best_progress': verdict.best,
                         'root_score': verdict.root, 'depth_score': verdict.depth,
                         'evidence': list(verdict.evidence), 'target_act': verdict.target_act,
                         'verdict_changes': record.changes})
        return {'records': len(self.records), 'stems': len(self.stems.parents) - 1, 'gates': rows,
                'counts': dict(self.counts), 'events': list(self.events), 'contended': self.contended(),
                'thresholds': {'min_entries': self.min_entries, 'near': self.near, 'far': self.far,
                               'endpoint_factor': self.endpoint_factor, 'escalate_after': self.escalate_after},
                **({'structural_prior': self.prior.snapshot()} if self.prior is not None else {}),
                'stem_identity': 'exact canonical non-combat decisions before the act; digests are display only',
                'scope': 'allocation diagnosis only; never a pruning rule, bound or infeasibility proof'}
