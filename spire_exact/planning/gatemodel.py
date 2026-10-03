"""Online gate-readiness model fitted on this seed's own native boss fights.

For every distinct boss-fight entry the search has executed, the entry state
(deck, upgrades, relics, potions, HP) and the native outcome are recorded. Two
small ridge regressions per gate estimate how much each owned item moves the
outcome:

- the *level* model regresses the outcome on the entry state;
- the *difference* model regresses (child - parent) outcome on the
  (child - parent) entry state for rollouts that deviated from a parent before
  the gate, which cancels everything the two share.

Their standardised coefficients order deck-building options for later
rollouts. Allocation signal only: never legality, a bound, a win claim or a
proof that a deck cannot pass. The models are fitted inside one search from
that search's own native results and discarded with it; nothing is trained
across seeds. All numbers depend on absorbed results in absorption order,
`solver_seed` and the request serial, so ordered runs stay reproducible.
"""
from __future__ import annotations
from collections import Counter
import hashlib
import json
import math
import struct
from .archive import classify_failure, combat_loss_progress

# Labels whose value is read directly from an item coefficient.
ITEM_HEADS = ('card', 'relic', 'upgrade', 'remove', 'transform', 'obtain', 'duplicate')
# Labels that never receive a learned value: potions are spent inside fights,
# reward and relic skips win by default when the offered items are avoided.
UNSCORED_HEADS = ('potion', 'use_potion', 'go', 'rest')
UNSCORED = frozenset({'treasure_skip', 'rewards_skip'})
# Labels valued by the pooled "one more card in the deck" estimate. A single
# card rarely reaches |z| >= 2 on its own, so every unproven card would be
# taken by the native default even when the deck-size effect as a whole is
# strongly negative (retained 10101010 run: z = -4.3 / -14.2 at the act 1 / 2
# boss with HP, potions, upgrades and relics as covariates, and already
# -4.3 / -7.2 after the first 80 distinct entries of each).
SIZE_LABELS = frozenset({'card_skip', 'buy_removal'})
SIZE_COLUMNS = ('size', 'hp', 'potions', 'upgrades', 'relics')
SIZE_CLAMP = 3.0


def entry_features(observation: dict) -> dict:
    """Sparse entry-state features. Item names double as host decision labels."""
    features = Counter()
    deck = observation.get('deck') or []
    for card in deck:
        features['card:' + card['id']] += 1
        if card.get('upgrade'):
            features['up:' + card['id']] += 1
    for relic in observation.get('relics') or []:
        features['relic:' + relic] += 1
    potions = [p for p in observation.get('potions') or [] if p]
    for potion in potions:
        features['potion:' + potion] += 1
    max_hp = max(1.0, float(observation.get('max_hp') or 1))
    features['#deck'] = len(deck) / 10.0
    features['#hp'] = float(observation.get('hp') or 0) / max_hp
    features['#maxhp'] = max_hp / 100.0
    features['#potions'] = float(len(potions))
    return dict(features)


def item_delta(before: dict, after: dict) -> dict:
    """Owned-item change between two consecutive observations."""
    a, b = entry_features(before), entry_features(after)
    delta = {}
    for key in set(a) | set(b):
        if key.startswith('#') or key.startswith('potion:'):
            continue
        change = b.get(key, 0) - a.get(key, 0)
        if change:
            delta[key] = change
    return delta


def boss_fights(result: dict) -> list:
    """(gate, entry index, entry observation, outcome) for every boss fight
    whose native outcome is known. Outcome: 1 + HP fraction kept after a
    survived fight, or the observed enemy-HP fraction removed in a lost one."""
    return [(row['gate'], row['index'], row['entry'], row['outcome']) for row in boss_fight_rows(result)]


def boss_fight_rows(result: dict) -> list:
    """`boss_fights` plus, for a lost fight, `lost` = (enemy HP removed, enemy
    HP of every life observed in that fight). A boss with several forms shows
    a later form only to the fights that reach it, so the per-fight fraction is
    not comparable between fights: 100 of 111 removed reads 0.90, while the
    whole first form plus 50 of a 212 HP second form reads 0.50. GateModels
    divides by the largest total seen at the gate instead."""
    status = classify_failure(result)
    if status not in ('NATIVE_ROUTE_DEATH', 'NATIVE_WIN_CANDIDATE', 'SEARCH_BUDGET', 'DECISION_BOUNDARY'):
        return []
    trace = result.get('trace', [])
    evidence = result.get('decision_evidence', [])[:len(trace)]
    terminal = result.get('observation') or {}
    fights = []
    ordinal = Counter()
    current = None
    for index, row in enumerate(evidence):
        obs = row.get('observation') or {}
        in_combat = row.get('phase') == 'combat' or (row.get('phase') == 'select_cards' and obs.get('turn') is not None)
        if in_combat and obs.get('room') == 'Boss':
            key = (obs.get('act'), obs.get('floor'))
            if current is None or current['key'] != key:
                act = int(obs.get('act') or 0)
                current = {'key': key, 'gate': (act, ordinal[act]), 'index': index, 'entry': obs, 'after': None}
                ordinal[act] += 1
                fights.append(current)
        elif current is not None and current['after'] is None and not in_combat:
            current['after'] = obs
    rows = []
    for fight in fights:
        entry = fight['entry']
        max_hp = max(1.0, float(entry.get('max_hp') or 1))
        terminal_here = (terminal.get('act'), terminal.get('floor')) == fight['key']
        lost = None
        if status == 'NATIVE_ROUTE_DEATH' and terminal_here:
            progress = combat_loss_progress(result)
            fraction = progress.get('hp_removed_fraction') if progress.get('available') else 0.0
            outcome = max(0.0, min(1.0, float(fraction or 0.0)))
            lost = (float(progress.get('observed_hp_removed') or 0.0), float(progress.get('observed_life_hp') or 0.0)) \
                if progress.get('available') else (0.0, 0.0)
        elif fight['after'] is not None:
            outcome = 1.0 + max(0.0, float(fight['after'].get('hp') or 0)) / max_hp
        elif fight is fights[-1] and status != 'NATIVE_ROUTE_DEATH' and terminal.get('turn') is None \
                and float(terminal.get('hp') or 0) > 0:
            # The request stopped at the first decision after the fight (or won the run).
            outcome = 1.0 + float(terminal.get('hp') or 0) / max_hp
        else:
            continue          # interrupted inside the fight: outcome unknown
        rows.append({'gate': fight['gate'], 'index': fight['index'], 'entry': entry, 'outcome': outcome, 'lost': lost})
    return rows


def aggregate_features(features: dict) -> dict:
    """The few dense columns of the pooled deck-size regression."""
    return {'size': features.get('#deck', 0.0), 'hp': features.get('#hp', 0.0), 'potions': features.get('#potions', 0.0),
            'upgrades': sum(value for key, value in features.items() if key.startswith('up:')) / 10.0,
            'relics': sum(value for key, value in features.items() if key.startswith('relic:')) / 10.0}


def _invert(matrix):
    """Gauss-Jordan inverse with partial pivoting; None when singular."""
    n = len(matrix)
    work = [list(row) + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(matrix)]
    for column in range(n):
        pivot = max(range(column, n), key=lambda r: abs(work[r][column]))
        if abs(work[pivot][column]) < 1e-12:
            return None
        work[column], work[pivot] = work[pivot], work[column]
        scale = work[column][column]
        work[column] = [value / scale for value in work[column]]
        for row in range(n):
            if row != column and work[row][column]:
                factor = work[row][column]
                work[row] = [a - factor * b for a, b in zip(work[row], work[column])]
    return [row[n:] for row in work]


def _ols(rows, keys):
    """Least squares on a few dense columns: (weights, z) from the full
    covariance, so correlated columns (size, upgrades, relics all grow with
    progress) do not lend each other significance. Columns without variance
    are left out. An ordering statistic like `_ridge`'s, not a test."""
    n = len(rows)
    means = {key: sum(features.get(key, 0.0) for features, _ in rows) / n for key in keys}
    spread = {key: sum((features.get(key, 0.0) - means[key]) ** 2 for features, _ in rows) for key in keys}
    used = [key for key in keys if spread[key] > 1e-9]
    k = len(used)
    if not k or n <= k + 2:
        return {}, {}
    target = sum(value for _, value in rows) / n
    normal = [[0.0] * k for _ in range(k)]
    moment = [0.0] * k
    for features, value in rows:
        x = [features.get(key, 0.0) - means[key] for key in used]
        for i in range(k):
            moment[i] += x[i] * (value - target)
            for j in range(i, k):
                normal[i][j] += x[i] * x[j]
    for i in range(k):
        for j in range(i):
            normal[i][j] = normal[j][i]
    inverse = _invert(normal)
    if inverse is None:
        return {}, {}
    weights = [sum(inverse[i][j] * moment[j] for j in range(k)) for i in range(k)]
    residual = 0.0
    for features, value in rows:
        fitted = target + sum(w * (features.get(key, 0.0) - means[key]) for w, key in zip(weights, used))
        residual += (value - fitted) ** 2
    variance = max(1e-12, residual / (n - k - 1))
    z = {key: weights[i] / math.sqrt(variance * inverse[i][i]) if inverse[i][i] > 0 else 0.0 for i, key in enumerate(used)}
    return dict(zip(used, weights)), z


def _ridge(rows, ridge, passes, warm=None):
    """Coordinate descent on sparse rows [(features, target)]: returns
    (intercept, weights, z). z = weight / (residual spread / sqrt(centred
    sum of squares + ridge)); an ordering statistic, not a significance test:
    sibling rollouts share most of their deck."""
    n = len(rows)
    columns = {}
    for i, (features, _) in enumerate(rows):
        for key, value in features.items():
            columns.setdefault(key, []).append((i, value))
    weights = {key: (warm or {}).get(key, 0.0) for key in columns}
    intercept = sum(target for _, target in rows) / n
    residual = [target - intercept for _, target in rows]
    for key, column in columns.items():
        w = weights[key]
        if w:
            for i, value in column:
                residual[i] -= w * value
    squares = {key: sum(value * value for _, value in column) for key, column in columns.items()}
    order = sorted(columns)
    for _ in range(passes):
        for key in order:
            column = columns[key]
            w = weights[key]
            rho = sum(value * (residual[i] + w * value) for i, value in column)
            new = rho / (squares[key] + ridge)
            change = new - w
            if change:
                for i, value in column:
                    residual[i] -= change * value
                weights[key] = new
        shift = sum(residual) / n
        intercept += shift
        residual = [r - shift for r in residual]
    spread = math.sqrt(max(1e-9, sum(r * r for r in residual) / max(1, n - 1)))
    z = {}
    for key, column in columns.items():
        mean = sum(value for _, value in column) / n
        centred = max(0.0, squares[key] - n * mean * mean)
        z[key] = weights[key] / (spread / math.sqrt(centred + ridge))
    return intercept, weights, z, spread


def _support(rows):
    """Centred sum of squares of every column of sparse rows: how much the
    table has seen an item vary (0 for an item every entry owns alike)."""
    n = len(rows)
    total, squares = {}, {}
    for features, _ in rows:
        for key, value in features.items():
            total[key] = total.get(key, 0.0) + value
            squares[key] = squares.get(key, 0.0) + value * value
    return {key: max(0.0, squares[key] - total[key] * total[key] / n) for key in total}


class _Gate:
    __slots__ = ('entries', 'pairs', 'weights', 'intercept', 'z', 'pair_weights', 'pair_z', 'dirty', 'fitted',
                 'pairs_fitted', 'residual', 'encounter', 'size_z', 'total', 'tables', 'probe_z', 'decks', 'support')

    def __init__(self):
        # entry prefix digest -> [features, outcome, enemy HP removed or None when survived]
        # (mutable row, shared with pairs; lost outcomes are rescaled at every fit)
        self.entries = {}
        self.total = 0.0        # largest enemy life total observed in a lost fight at this gate
        self.pairs = []         # (child row, parent row) for rollouts that deviated before this gate
        self.weights = {}
        self.intercept = 0.0
        self.z = {}
        self.pair_weights = {}
        self.pair_z = {}
        self.dirty = 0
        self.fitted = 0
        self.pairs_fitted = 0
        self.residual = None
        self.encounter = None
        self.size_z = 0.0       # pooled z of one more card (per ten cards) at this gate
        # Synthetic probe tables (probes.py): table id -> {'base': outcome, 'rows': {label: outcome}}.
        # Kept apart from `entries`: a probe never becomes a row of the regressions above.
        self.tables = {}
        self.probe_z = {}       # label -> standardised probe value, recomputed when a table changes
        self.decks = []         # (Counter of card ids, entry row) in absorption order, read by family()
        self.support = {}       # feature -> centred sum of squares at the last fit, read by novelty()


class GateModels:
    def __init__(self, solver_seed: int = 0, *, ridge: float = 3.0, minimum: int = 24, refresh: int = 16,
                 noise: float = 0.75, passes: int = 12, size: bool = False, probes: bool = False,
                 probe_floor: float = 0.05, probe_minimum: int = 6):
        self.solver_seed = int(solver_seed)
        self.ridge = ridge
        self.minimum = minimum
        self.refresh = refresh
        self.noise = noise
        self.passes = passes
        # Value card_skip / buy_removal from the pooled deck-size effect. Off by
        # default: no end-to-end comparison yet. It was on by accident in
        # frozen-i039-infra, where the sign differed by seed (10101010 retained
        # run -4.3 / -14.2 at the act 1 / 2 boss, 1741222413 +4.3 / +3.4).
        self.size = bool(size)
        # Read synthetic probe tables (add_probes) as a third source of label
        # values. Off by default: component evidence only (iteration-044).
        # probe_floor is the smallest spread a table's deltas are divided by, in
        # outcome units; probe_minimum the edits a table needs before it is read.
        self.probes = bool(probes)
        self.probe_floor = float(probe_floor)
        self.probe_minimum = int(probe_minimum)
        self.gates = {}             # (act, ordinal) -> _Gate
        self.sources = {}           # evaluation label -> {gate id: entry row}
        self.deltas = {}            # non-item label -> first observed owned-item delta
        self.version = 0            # bumps whenever any gate is refitted
        self._cache = {}
        self._labels = None

    # ---- data -----------------------------------------------------------
    def add(self, result: dict, label: str | None = None, parent: str | None = None) -> int:
        trace = result.get('trace', [])
        evidence = result.get('decision_evidence', [])[:len(trace)]
        self._learn_deltas(trace, evidence, result.get('observation') or {})
        fights = boss_fight_rows(result)
        if not fights:
            return 0
        wanted = {fight['index'] for fight in fights}
        last = max(wanted)
        digests = {}
        running = hashlib.blake2b(digest_size=12)
        for index, action in enumerate(trace[:last]):
            if index in wanted:
                digests[index] = running.copy().digest()
            running.update(json.dumps(action, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
            running.update(b'\n')
        digests[last] = running.digest()
        mine = {}
        theirs = self.sources.get(parent) or {}
        changed = 0
        for fight in fights:
            gate_id, index, entry, outcome, lost = fight['gate'], fight['index'], fight['entry'], fight['outcome'], fight['lost']
            gate = self.gates.get(gate_id)
            if gate is None:
                gate = self.gates[gate_id] = _Gate()
            if gate.encounter is None:
                enemies = entry.get('enemies') or []
                gate.encounter = enemies[0].get('id') if enemies else None
            removed = None
            if lost is not None:
                removed = lost[0]
                if lost[1] > gate.total:
                    # A later form (or a larger enemy group) was seen for the
                    # first time: every lost fight at this gate is rescaled.
                    gate.total = lost[1]
                    gate.dirty += 1
                outcome = min(1.0, removed / gate.total) if gate.total > 0 else 0.0
            key = digests[index]
            row = gate.entries.get(key)
            if row is None:
                row = gate.entries[key] = [entry_features(entry), outcome, removed]
                gate.decks.append((Counter(card['id'] for card in entry.get('deck') or []), row))
                gate.dirty += 1
                changed += 1
                other = theirs.get(gate_id)
                if other is not None and other is not row:
                    gate.pairs.append((row, other))
            elif (removed is None and (row[2] is not None or outcome > row[1])) or \
                    (removed is not None and row[2] is not None and removed > row[2]):
                # The same entry solved better (stronger tactical plan): an
                # entry's value is the best native outcome observed for it.
                row[1], row[2] = outcome, removed
                gate.dirty += 1
                changed += 1
            mine[gate_id] = row
            if gate.dirty >= self.refresh and len(gate.entries) >= self.minimum:
                self._fit(gate)
        if label is not None:
            self.sources[label] = mine
        return changed

    def _learn_deltas(self, trace, evidence, terminal):
        for index, row in enumerate(evidence):
            options = row.get('option_labels')
            if not options:
                continue
            wanted = json.dumps(trace[index], sort_keys=True, separators=(',', ':'))
            chosen = None
            for candidate, labels in zip(row.get('available_actions', []), options):
                if json.dumps(candidate, sort_keys=True, separators=(',', ':')) == wanted:
                    chosen = labels
                    break
            if not chosen or len(chosen) != 1:
                continue
            label = chosen[0]
            head = label.partition(':')[0]
            if label in self.deltas or label in UNSCORED or head in ITEM_HEADS or head in UNSCORED_HEADS:
                continue
            after = evidence[index + 1].get('observation') if index + 1 < len(evidence) else terminal
            before = row.get('observation') or {}
            if not after or not after.get('deck'):
                continue
            self.deltas[label] = item_delta(before, after)
            self._labels = None

    # ---- synthetic probes ----------------------------------------------
    def add_probes(self, gate_id, table, rows) -> int:
        """Outcomes of synthetic probes of one base entry at a gate (probes.py).
        rows = [(label, outcome)]: label None is the unedited base, otherwise the
        host label of the one-card edit; outcome is probes.probe_outcome(...) or
        None for a probe that did not finish. A table is read once it has its
        base and `probe_minimum` edits. Probe outcomes stay out of the entry
        regressions and never move the gate's real life total."""
        gate = self.gates.get(gate_id)
        if gate is None:
            gate = self.gates[gate_id] = _Gate()
        slot = gate.tables.setdefault(table, {'base': None, 'rows': {}})
        added = 0
        for label, outcome in rows:
            if outcome is None:
                continue
            if label is None:
                slot['base'] = outcome
            else:
                slot['rows'][label] = outcome
            added += 1
        if added:
            self._probe_refresh(gate)
        return added

    def _probe_refresh(self, gate: _Gate) -> None:
        """label -> delta against the table's base, in units of the table's own
        robust spread (1.4826 x median absolute deviation, at least probe_floor).
        Several tables of one gate are combined as sum / sqrt(count)."""
        outcomes = [o for slot in gate.tables.values() for o in [slot['base'], *slot['rows'].values()] if o is not None]
        total = max([gate.total] + [o['lost'][1] for o in outcomes if o.get('lost')])

        def value(outcome):
            if outcome['won']:
                return outcome['outcome']
            return min(1.0, outcome['lost'][0] / total) if total > 0 else 0.0

        scores = {}
        for _, slot in sorted(gate.tables.items()):
            if slot['base'] is None or len(slot['rows']) < self.probe_minimum:
                continue
            base = value(slot['base'])
            deltas = {label: value(outcome) - base for label, outcome in slot['rows'].items()}
            ordered = sorted(deltas.values())
            median = ordered[len(ordered) // 2]
            spread = sorted(abs(d - median) for d in ordered)[len(ordered) // 2]
            scale = max(self.probe_floor, 1.4826 * spread)
            for label, delta in deltas.items():
                scores.setdefault(label, []).append(delta / scale)
        gate.probe_z = {label: sum(values) / math.sqrt(len(values)) for label, values in scores.items()}
        self.version += 1
        self._cache = {}
        self._labels = None

    # ---- fit ------------------------------------------------------------
    def _fit(self, gate: _Gate) -> None:
        for row in gate.entries.values():
            if row[2] is not None:                 # lost fight: one common denominator for the whole gate
                row[1] = min(1.0, row[2] / gate.total) if gate.total > 0 else 0.0
        rows = [(row[0], row[1]) for row in gate.entries.values()]
        gate.intercept, gate.weights, gate.z, gate.residual = _ridge(rows, self.ridge, self.passes, gate.weights)
        gate.support = _support(rows)
        gate.fitted = len(rows)
        if self.size:
            _, pooled = _ols([(aggregate_features(features), outcome) for features, outcome in rows], SIZE_COLUMNS)
            gate.size_z = pooled.get('size', 0.0)
        if len(gate.pairs) >= self.minimum:
            differences = []
            for child, parent in gate.pairs:
                a, b = child[0], parent[0]
                delta = {key: a.get(key, 0) - b.get(key, 0) for key in set(a) | set(b) if a.get(key, 0) != b.get(key, 0)}
                differences.append((delta, child[1] - parent[1]))
            _, gate.pair_weights, gate.pair_z, _ = _ridge(differences, self.ridge, self.passes, gate.pair_weights)
            gate.pairs_fitted = len(differences)
        gate.dirty = 0
        self.version += 1
        self._cache = {}
        self._labels = None
        if gate.tables:
            self._probe_refresh(gate)          # the gate's life total may have grown

    def refit(self) -> None:
        """Fit every gate that has enough entries (end of run / reporting)."""
        for gate in self.gates.values():
            if gate.dirty and len(gate.entries) >= self.minimum:
                self._fit(gate)

    # ---- queries --------------------------------------------------------
    def predict(self, gate_id, observation: dict) -> float | None:
        gate = self.gates.get(gate_id)
        if gate is None or not gate.fitted:
            return None
        return gate.intercept + sum(value * gate.weights.get(key, 0.0) for key, value in entry_features(observation).items())

    @staticmethod
    def _item(gate: _Gate, key: str) -> float:
        """Standardised value of owning one more `key` at this gate: the mean
        of the level and difference models once both exist."""
        value = gate.z.get(key, 0.0)
        if gate.pairs_fitted:
            value = 0.5 * (value + gate.pair_z.get(key, 0.0))
        return value

    def _label_at(self, gate: _Gate, label: str) -> float:
        if label in UNSCORED:
            return 0.0
        if label in SIZE_LABELS:
            # Not adding a card (or paying to drop one) is worth the opposite
            # of the pooled per-card effect.
            return -gate.size_z if self.size else 0.0
        head, _, name = label.partition(':')
        if head in UNSCORED_HEADS:
            return 0.0
        if head in ('card', 'obtain', 'duplicate'):
            return self._item(gate, 'card:' + name)
        if head == 'relic':
            return self._item(gate, label)
        if head == 'upgrade':
            return self._item(gate, 'up:' + name)
        if head in ('remove', 'transform'):
            return -self._item(gate, 'card:' + name)
        delta = self.deltas.get(label)
        if not delta:
            return 0.0
        return sum(change * self._item(gate, key) for key, change in delta.items()) / math.sqrt(len(delta))

    def _informed(self, gate: _Gate) -> bool:
        return bool(gate.fitted or (self.probes and gate.probe_z))

    def _value_at(self, gate: _Gate, label: str) -> float:
        """Value of a label at one gate: the regressions on real entries, the
        probe table, or their mean when both say something about the label."""
        learned = self._label_at(gate, label) if gate.fitted else 0.0
        probe = gate.probe_z.get(label) if self.probes else None
        if probe is None:
            return learned
        return probe if learned == 0.0 else 0.5 * (learned + probe)

    def labels(self) -> list:
        if self._labels is None:
            names = {label for label, delta in self.deltas.items() if delta}
            for gate in self.gates.values():
                if self.probes:
                    names.update(gate.probe_z)
                for key in gate.z:
                    head, _, name = key.partition(':')
                    if head == 'card':
                        names.update((key, 'remove:' + name, 'transform:' + name, 'obtain:' + name, 'duplicate:' + name))
                    elif head == 'up':
                        names.add('upgrade:' + name)
                    elif head == 'relic':
                        names.add(key)
                if self.size and gate.size_z:
                    names.update(SIZE_LABELS)
            self._labels = sorted(names)
        return self._labels

    def z(self, label: str, act: int) -> float:
        """Combined standardised value of a label for every fitted gate at or
        after `act` (equal weights: each remaining gate must be passed)."""
        key = (label, act)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        fitted = [gate for gate_id, gate in sorted(self.gates.items()) if self._informed(gate) and gate_id[0] >= act]
        value = sum(self._value_at(gate, label) for gate in fitted) / math.sqrt(len(fitted)) if fitted else 0.0
        if label in SIZE_LABELS:
            # One step at most without the sampling noise: skip unless a card
            # has evidence of its own; never "skip everything but the best".
            value = max(-SIZE_CLAMP, min(SIZE_CLAMP, value))
        self._cache[key] = value
        return value

    def best(self, labels, act: int, optimism: float = 0.0) -> float:
        """Largest value among the labels of one option (0 when unlabelled).
        With `optimism`, a label counts for its value plus that many standard
        errors scaled by how little the gates have seen of it (novelty)."""
        if optimism:
            return max((self.z(label, act) + optimism * self.novelty(label, act) for label in labels), default=0.0)
        return max((self.z(label, act) for label in labels), default=0.0)

    def _keys(self, label: str) -> tuple:
        """Entry features a label changes; () for labels that never get a value."""
        if label in UNSCORED or label in SIZE_LABELS:
            return ()
        head, _, name = label.partition(':')
        if head in UNSCORED_HEADS:
            return ()
        if head in ('card', 'obtain', 'duplicate', 'remove', 'transform'):
            return ('card:' + name,)
        if head == 'relic':
            return (label,)
        if head == 'upgrade':
            return ('up:' + name,)
        return tuple(sorted(self.deltas.get(label) or ()))

    def novelty(self, label: str, act: int) -> float:
        """How little the fitted gates at or after `act` have seen of the items
        a label changes: sqrt(ridge / (centred sum of squares + ridge)), the
        item's standard error over that of an unseen item. 1 = never seen,
        towards 0 = seen in many entries; 0 for labels without a value and
        before any gate is fitted. It tells an item the ridge left at z = 0
        for want of data from one that many entries say is neutral.
        Allocation signal only."""
        key = ('novelty', label, act)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        gates = [gate for gate_id, gate in sorted(self.gates.items()) if gate.fitted and gate_id[0] >= act]
        keys = self._keys(label) if gates else ()
        value = sum(math.sqrt(self.ridge / (gate.support.get(item, 0.0) + self.ridge)) for gate in gates for item in keys) \
            / (len(gates) * len(keys)) if keys else 0.0
        self._cache[key] = value
        return value

    # ---- deck families --------------------------------------------------
    def family(self, gate_id, deck, percent: int = 75, memo: list | None = None) -> tuple:
        """(entries, passed): the real entries of one gate whose deck is alike
        `deck` (multiset Jaccard of card ids >= percent) and how many of them
        survived the fight. One fight says little about a deck (shuffle
        noise); the record of every entry of its family is the multi-sample
        estimate, normalised by attempts. `memo` ([entries scanned, alike
        rows], owned by the caller) makes repeated queries incremental.
        Synthetic probes are never counted. Allocation signal only: a family
        without a pass has not been shown unable to pass."""
        gate = self.gates.get(gate_id)
        if gate is None:
            return 0, 0
        if memo is None:
            memo = [0, []]
        rows = memo[1]
        for other, row in gate.decks[memo[0]:]:
            union = sum((deck | other).values())
            if union == 0 or 100 * sum((deck & other).values()) >= percent * union:
                rows.append(row)
        memo[0] = len(gate.decks)
        return len(rows), sum(row[2] is None for row in rows)

    @staticmethod
    def optimistic(entries: int, passed: int) -> float:
        """Pass rate of a family under a uniform prior, one posterior standard
        deviation above its mean: 0.79 without entries, 0.19 after 0 of 8,
        0.06 after 0 of 30, 0.55 after 3 of 8. Little evidence keeps a family
        worth trying; much evidence without a pass lowers its share roughly as
        1 / entries and never to zero."""
        mean = (passed + 1.0) / (entries + 2.0)
        return mean + math.sqrt(mean * (1.0 - mean) / (entries + 3.0))

    def _gauss(self, serial: int, label: str) -> float:
        digest = hashlib.sha256(f'{self.solver_seed}:{serial}:{label}'.encode('utf-8')).digest()
        a, b = struct.unpack('>QQ', digest[:16])
        return math.sqrt(-2.0 * math.log((a + 1) / 2.0 ** 64)) * math.cos(2.0 * math.pi * b / 2.0 ** 64)

    def prior(self, serial: int) -> dict:
        """label -> tier per act for one request. The standardised value is
        perturbed by reproducible Gaussian noise; |value| >= 2 gives tier +-1
        and >= 4 gives +-2, so strong evidence is nearly always applied, weak
        evidence sometimes, and every rollout samples a different policy."""
        acts = sorted({gate_id[0] for gate_id, gate in self.gates.items() if self._informed(gate)})
        if not acts:
            return {}
        last = acts[-1]
        tiers = {}
        for label in self.labels():
            noise = self.noise * self._gauss(serial, label)
            row = []
            for act in range(last + 1):
                value = self.z(label, act)
                if value == 0.0:
                    row.append(0)
                    continue
                value += noise
                row.append(2 if value >= 4 else 1 if value >= 2 else -2 if value <= -4 else -1 if value <= -2 else 0)
            if any(row):
                tiers[label] = row
        return tiers

    def snapshot(self, limit: int = 12) -> dict:
        gates = []
        for gate_id, gate in sorted(self.gates.items()):
            merged = {key: self._item(gate, key) for key in gate.z if not key.startswith('#')}
            ranked = sorted(merged.items(), key=lambda item: (-item[1], item[0]))
            outcomes = [(min(1.0, row[2] / gate.total) if gate.total > 0 else 0.0) if row[2] is not None else row[1]
                        for row in gate.entries.values()]
            gates.append({'act': gate_id[0], 'ordinal': gate_id[1], 'encounter': gate.encounter, 'entries': len(gate.entries),
                          'fitted_entries': gate.fitted, 'pairs': len(gate.pairs), 'fitted_pairs': gate.pairs_fitted,
                          'survived_entries': sum(o >= 1.0 for o in outcomes),
                          'best_outcome': max(outcomes) if outcomes else None, 'residual_spread': gate.residual,
                          'size_z': round(gate.size_z, 2),
                          'top': [[k, round(v, 2)] for k, v in ranked[:limit]],
                          'bottom': [[k, round(v, 2)] for k, v in ranked[-limit:][::-1]]})
            if self.probes:
                probed = sorted(gate.probe_z.items(), key=lambda item: (-item[1], item[0]))
                gates[-1].update(probe_tables=len(gate.tables), probe_labels=len(gate.probe_z), probes_are='synthetic; allocation only',
                                 probe_top=[[k, round(v, 2)] for k, v in probed[:limit]],
                                 probe_bottom=[[k, round(v, 2)] for k, v in probed[-limit:][::-1]])
        return {'gates': gates, 'learned_option_deltas': len(self.deltas), 'version': self.version,
                'scope': 'same-seed allocation model; never a bound, proof or cross-seed model'}
