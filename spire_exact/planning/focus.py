"""Elite-guided macro scheduling on top of the indexed weighted explorer.

The weighted scheduler spreads evaluations evenly over every (act, floor,
phase) cell of every executed trajectory. On a three-hour A10 run that left
the best lineage waiting for most of the run. This scheduler spends a fixed
share of evaluations on the trajectories that got furthest: it revisits their
decisions starting next to the fatal fight and moves backwards floor by floor,
trying the options a per-seed gate model ranks highest first.

Heuristic allocation only. A trajectory's rank, a label's value and the order
of alternatives never exclude anything: every alternative stays reachable
through the explorer share, and prefix identity is still the exact trie of
full canonical actions shared with the explorer.
"""
from __future__ import annotations
from bisect import insort
from collections import Counter
from ..canonical import canonical
from .archive import classify_failure, utility
from .gatemodel import boss_fight_rows
from .indexed_strategy import IndexedStrategicScheduler
from .strategy import FailureAnalyzer, strategic_phase

EXCLUDED_KINDS = ('discard_potion', 'reward', 'rewards_skip')


class _Source:
    __slots__ = ('label', 'quality', 'order', 'death_floor', 'diagnosis', 'sites', 'exhausted', 'picks', 'deck', 'gate', 'memo', 'carried')

    def __init__(self, label, quality, order, death_floor, diagnosis, sites, deck=None, gate=None):
        self.label, self.quality, self.order = label, quality, order
        self.death_floor, self.diagnosis, self.sites = death_floor, diagnosis, sites
        self.exhausted = False
        self.picks = 0
        self.deck = deck            # Counter of card ids at the end of the trajectory (cluster cap, family record)
        self.gate = gate            # boss gate the trajectory still has to pass (family record only)
        self.memo = [0, []]         # incremental scan of that gate's entries (GateModels.family)
        self.carried = 0.0          # HP share carried into the fatal fight when it is a later boss of its act (carry)


class FocusScheduler:
    """`focus` : `explore` evaluations go to elite trajectories : weighted explorer.

    elites      trajectories that share the focus evaluations (rank r gets a
                share proportional to 1 / (r + 1));
    pool        best trajectories whose decision sites are kept in memory;
    site_cap    explorer-side deferral for very wide menus (see explorer);
    cluster_cap at most this many elites whose final decks are alike (multiset
                Jaccard of card ids >= cluster_percent). 0 keeps the best
                trajectories regardless of their decks, which on a three-hour
                run put 80 % of the entries of one gate into two near-identical
                decks. The cap only moves focus evaluations between
                trajectories; a crowded-out trajectory keeps every branch it
                has in the explorer.
    family      an elite's share is its rank share times the optimistic pass
                rate of its deck family at the boss gate it still has to pass
                (GateModels.family / optimistic: every real entry of that gate
                with an alike deck, normalised by attempts). Ranking by the
                furthest single trajectory favours the family with the most
                entries; with the record, a family that keeps failing gives
                way to families with fewer entries. No share ever reaches 0.
    optimism    percentage of one standard error added to the value of an
                option for items the gate model has seen little of
                (GateModels.novelty): little-tried options are tried earlier,
                well-tried neutral ones later. 0 = the model value alone.
    carry       elite places are handed out alternately from two orders of
                the pool: the pool order, and the same order with the
                trajectories that died in a later boss of an act (no rest
                between the bosses) ranked by the HP they carried into that
                fight before the progress of the lost fight. Trajectories
                that died on different floors keep their order, and the pool
                itself is unchanged. Retained runs (iteration-054): the HP a
                trajectory carried into the second final boss predicted its
                descendants' entries at least as well as the progress of the
                fight it lost, and neither order replaced the other.
    """

    def __init__(self, scope_prefix=None, *, models=None, elites: int = 8, pool: int = 48,
                 focus: int = 3, explore: int = 1, site_cap: int | None = 12,
                 cluster_cap: int = 0, cluster_percent: int = 75, family: bool = False, optimism: int = 0,
                 carry: bool = False):
        if min(elites, pool, focus) < 1 or explore < 0 or pool < elites:
            raise ValueError('invalid focus scheduler configuration')
        if type(cluster_cap) is not int or cluster_cap < 0 or type(cluster_percent) is not int or not 1 <= cluster_percent <= 100:
            raise ValueError('invalid deck cluster configuration')
        if type(family) is not bool or type(optimism) is not int or not 0 <= optimism <= 400 or ((family or optimism) and models is None):
            raise ValueError('invalid family record configuration')
        if type(carry) is not bool:
            raise ValueError('invalid carried HP configuration')
        self.explorer = IndexedStrategicScheduler(scope_prefix, site_cap)
        self.scope_length = len(scope_prefix or [])
        self.models = models
        self.elites, self.pool_size = elites, pool
        self.focus_share, self.explore_share = focus, explore
        self.cluster_cap, self.cluster_percent = cluster_cap, cluster_percent
        self.family, self.optimism = family, optimism
        self.carry = carry
        self.crowded_out = 0        # trajectories refused or evicted because their deck family was full
        self.pool = []              # sorted [(negated quality, order, label)]
        self.sources = {}           # label -> _Source (pool members only)
        self.qualities = set()      # quality tuples already represented in the pool
        self.order = 0
        self.focus_picks = 0
        self.explore_picks = 0
        self.promotions = 0

    # ---- intake ---------------------------------------------------------
    def add(self, result, label):
        self.explorer.add(result, label)
        if classify_failure(result) != 'NATIVE_ROUTE_DEATH' or not result.get('trace'):
            return
        quality = utility(result)
        # An equal outcome adds a parallel copy of the same neighbourhood, not
        # a better starting point: only the first trajectory of a quality is kept.
        if quality in self.qualities:
            return
        key = tuple(-x for x in quality)
        deck = victim = None
        if self.cluster_cap or self.family:
            deck = Counter(card.get('id') for card in (result.get('observation') or {}).get('deck') or [])
        if self.cluster_cap:
            if len(self.pool) >= self.pool_size:
                victim = self._victim(key, deck)
                if victim is None:
                    return
        elif len(self.pool) >= self.pool_size and key >= self.pool[-1][0]:
            return
        trace = result['trace']
        evidence = result.get('decision_evidence', [])[:len(trace)]
        path = self.explorer.prefixes.index(trace)
        death_floor = int((result.get('observation') or {}).get('floor') or 0)
        sites = []
        rooms = set()
        for i, row in enumerate(evidence):
            if i < self.scope_length or not strategic_phase(row):
                continue
            obs = row.get('observation') or {}
            floor, act = int(obs.get('floor') or 0), int(obs.get('act') or 0)
            room = (obs.get('act'), floor, row.get('phase'), (obs.get('selection') or {}).get('purpose'))
            if room in rooms and row.get('phase') != 'card_reward':
                continue
            rooms.add(room)
            chosen = self.explorer.prefixes.actions[path[i + 1]]
            labels = row.get('option_labels')
            alternatives = []
            for position, action in enumerate(row.get('available_actions', [])):
                if action.get('kind') in EXCLUDED_KINDS or canonical(action) == chosen:
                    continue
                names = tuple(labels[position]) if labels and position < len(labels) else ()
                alternatives.append((self.explorer.prefixes.child(path[i], action), names,
                                     6 if action.get('kind') == 'use_potion' else 0))
            if alternatives:
                sites.append((i, floor, act, row['phase'], alternatives))
        self.order += 1
        gate = None
        if self.family:
            # Dead in a boss fight: that gate. Dead elsewhere: the next boss of the act.
            act = int((result.get('observation') or {}).get('act') or 0)
            gate = (act, sum(row['gate'][0] == act and row['lost'] is None for row in boss_fight_rows(result)))
        source = _Source(label, quality, self.order, death_floor, FailureAnalyzer.analyze(result), sites, deck, gate)
        if self.carry:
            fights = boss_fight_rows(result)
            if fights and fights[-1]['lost'] is not None and fights[-1]['gate'][1] > 0:
                entry = fights[-1]['entry']
                source.carried = float(entry.get('hp') or 0) / max(1.0, float(entry.get('max_hp') or 1))
        self.sources[label] = source
        self.qualities.add(quality)
        insort(self.pool, (key, source.order, label))
        self.promotions += int(self.pool[0][2] == label)
        if victim is not None:
            dropped = self.sources.pop(victim)
            self.qualities.discard(dropped.quality)
            self.pool.remove((tuple(-x for x in dropped.quality), dropped.order, victim))
        while len(self.pool) > self.pool_size:
            _, _, dropped = self.pool.pop()
            self.qualities.discard(self.sources.pop(dropped).quality)

    # ---- deck families --------------------------------------------------
    def _alike(self, a, b):
        union = sum((a | b).values())
        return union == 0 or 100 * sum((a & b).values()) >= self.cluster_percent * union

    def _crowded(self, deck, chosen):
        """Do `cluster_cap` of the already chosen decks look like this one?"""
        count = 0
        for other in chosen:
            if self._alike(deck, other):
                count += 1
                if count >= self.cluster_cap:
                    return True
        return False

    def _victim(self, key, deck):
        """The pool is full and a new trajectory (key, deck) arrives: label of
        the pool member to drop, or None to leave the newcomer out. The worst
        trajectory whose deck family already has `cluster_cap` better members
        goes first; without one, the worst trajectory overall."""
        rows = [(k, self.sources[label].deck, label) for k, _, label in self.pool]
        position = next((i for i, row in enumerate(rows) if key < row[0]), len(rows))
        rows.insert(position, (key, deck, None))
        chosen, crowded = [], False
        victim = rows[-1][2]
        for _, other, label in rows:
            if label is not None and self.sources[label].exhausted:
                continue                            # holds no family place (see _elite_sources)
            if self._crowded(other, chosen):
                victim, crowded = label, True       # the last crowded row is the worst one
            else:
                chosen.append(other)
        self.crowded_out += int(crowded)
        return victim

    def _places(self):
        """Pool labels in the order elite places are handed out."""
        labels = [label for _, _, label in self.pool]
        if not self.carry:
            return labels

        def key(label):
            source = self.sources[label]
            # Act and floor first: carried HP only orders trajectories that died in the same fight.
            return tuple(-x for x in source.quality[:4]) + (-source.carried,) + tuple(-x for x in source.quality[4:]), source.order
        merged, taken = [], set()
        for pair in zip(labels, sorted(labels, key=key)):
            for label in pair:
                if label not in taken:
                    taken.add(label)
                    merged.append(label)
        return merged

    def _elite_sources(self):
        elites = []
        if not self.cluster_cap:
            for label in self._places():
                source = self.sources[label]
                if source.exhausted:
                    continue
                elites.append(source)
                if len(elites) == self.elites:
                    break
            return elites
        decks = []
        for label in self._places():
            source = self.sources[label]
            # An exhausted trajectory holds no place: the next one of its family steps in.
            if source.exhausted or self._crowded(source.deck, decks):
                continue
            elites.append(source)
            decks.append(source.deck)
            if len(elites) == self.elites:
                break
        return elites

    # ---- selection ------------------------------------------------------
    def _value(self, names, act):
        if self.optimism and names:
            return self.models.best(names, act, self.optimism / 100.0)
        return self.models.best(names, act) if self.models is not None and names else 0.0

    def _record(self, source):
        """(entries, passed) of the source's deck family at its gate."""
        return self.models.family(source.gate, source.deck, self.cluster_percent, source.memo)

    def _candidate(self, source):
        """Cheapest pending alternative of one trajectory. Cost counts floors
        back from the fatal floor plus two for every option of the same
        decision already tried or ranked better, shifted by the option's
        learned value: the search widens a late decision and walks back to
        earlier floors at the same time."""
        best = None
        pending = self.explorer.pending
        for index, floor, act, phase, alternatives in source.sites:
            options = [(-self._value(names, act), position, key, penalty)
                       for position, (key, names, penalty) in enumerate(alternatives) if pending(key)]
            if not options:
                continue
            options.sort()
            back = max(0, source.death_floor - floor)
            tried = len(alternatives) - len(options)
            negative, position, key, penalty = options[0]
            value = max(-4.0, min(4.0, -negative))
            row = (back + 2 * tried + penalty - 2.0 * value, -index, position, key, index, floor, act, phase)
            if best is None or row < best:
                best = row
        return best

    def _focus(self):
        elites = self._elite_sources()
        # Rank-weighted fair share: the trajectory furthest below its share goes next.
        weights = [1.0 / (rank + 1) for rank in range(len(elites))]
        if self.family:
            weights = [weight * self.models.optimistic(*self._record(source)) for weight, source in zip(weights, elites)]
        total = sum(weights)
        ranked = sorted(range(len(elites)), key=lambda rank: (
            elites[rank].picks - (self.focus_picks + 1) * weights[rank] / total, rank))
        for rank in ranked:
            source = elites[rank]
            candidate = self._candidate(source)
            if candidate is None:
                source.exhausted = True
                continue
            _, _, _, key, index, floor, _, phase = candidate
            self.explorer.take(key)
            source.picks += 1
            self.focus_picks += 1
            return {'prefix': self.explorer.prefixes.restore(key), 'category': 'focus', 'phase': phase, 'floor': floor,
                    'index': index, 'source': source.label, 'diagnosis': source.diagnosis, 'elite_rank': rank}
        return None

    def _explore(self):
        group = self.explorer.next()
        if group is not None:
            self.explore_picks += 1
        return group

    def next(self):
        focus_first = self.explore_share == 0 or self.focus_picks * self.explore_share <= self.explore_picks * self.focus_share
        if focus_first:
            return self._focus() or self._explore()
        return self._explore() or self._focus()

    def snapshot(self):
        report = self._snapshot()
        if self.family:
            # The elites the next focus evaluation is shared between, with the record behind each share.
            rows = []
            for source in self._elite_sources():
                entries, passed = self._record(source)
                rows.append({'label': source.label, 'gate': list(source.gate), 'family_entries': entries, 'family_passed': passed,
                             'optimistic_pass_rate': round(self.models.optimistic(entries, passed), 4), 'focus_evaluations': source.picks})
            report.update(focus_family=rows, family_record_is='real entries with an alike deck at the gate; allocation only, never a bound')
        if self.optimism:
            report['focus_optimism_percent'] = self.optimism
        if self.carry:
            report.update(focus_carry=[{'label': source.label, 'carried_hp': round(source.carried, 4), 'focus_evaluations': source.picks}
                                       for source in self._elite_sources()],
                          carried_hp_is='HP share carried into a lost later boss of an act; elite places alternate between '
                                        'the pool order and that order; allocation only, never a bound')
        return report

    def _snapshot(self):
        if not self.cluster_cap:
            elites = [self.sources[label] for _, _, label in self.pool[:self.elites]]
            return {**self.explorer.snapshot(), 'scheduler': 'focus', 'focus_evaluations': self.focus_picks,
                    'explorer_evaluations': self.explore_picks, 'elite_pool': len(self.pool), 'best_trajectory_changes': self.promotions,
                    'elites': [{'label': s.label, 'heuristic_quality': list(s.quality), 'focus_evaluations': s.picks,
                                'exhausted': s.exhausted} for s in elites],
                    'elite_rank_is_proof': False}
        elites = self._elite_sources()
        # Family = position of the first elite with a deck like this one.
        families = []
        for index, source in enumerate(elites):
            families.append(next((families[i] for i in range(index) if self._alike(source.deck, elites[i].deck)), index))
        return {**self.explorer.snapshot(), 'scheduler': 'focus', 'focus_evaluations': self.focus_picks,
                'explorer_evaluations': self.explore_picks, 'elite_pool': len(self.pool), 'best_trajectory_changes': self.promotions,
                'elites': [{'label': s.label, 'heuristic_quality': list(s.quality), 'focus_evaluations': s.picks,
                            'exhausted': s.exhausted, 'deck_cards': sum(s.deck.values()), 'deck_family': family}
                           for s, family in zip(elites, families)],
                'deck_cluster_cap': self.cluster_cap, 'deck_cluster_percent': self.cluster_percent,
                'elite_deck_families': len(set(families)), 'crowded_out_trajectories': self.crowded_out,
                'elite_rank_is_proof': False}
