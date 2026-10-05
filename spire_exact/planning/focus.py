"""Elite-guided macro scheduling on top of the indexed weighted explorer.

The weighted scheduler spreads evaluations evenly over every (act, floor,
phase) cell of every executed trajectory. On a three-hour A10 run that left
the best lineage waiting for most of the run. This scheduler spends a fixed
share of evaluations on the trajectories that got furthest: it revisits their
decisions starting next to the fatal fight and moves backwards floor by floor,
trying the options a per-seed gate model ranks highest first.

Heuristic allocation only: rankings are never infeasibility proofs. Legacy
wide-menu sampling can leave some alternatives unqueued; i085 may retain and
progressively activate them. Prefix identity remains the exact trie of full
canonical actions shared with the explorer. Finite-budget coverage is not promised.
"""
from __future__ import annotations
from bisect import insort
from collections import Counter
import json
import heapq
import math
from ..canonical import canonical
from .archive import classify_failure, utility
from .gatemodel import boss_fight_rows
from .indexed_strategy import IndexedStrategicScheduler
from .lineage import LineageBackoff
from .strategy import FailureAnalyzer, strategic_phase

EXCLUDED_KINDS = ('discard_potion', 'reward', 'rewards_skip')


class _Source:
    __slots__ = ('label', 'quality', 'order', 'death_floor', 'diagnosis', 'sites', 'exhausted', 'picks', 'deck', 'gate', 'memo', 'carried', 'act', 'f1_entry', 'retention_key')

    def __init__(self, label, quality, order, death_floor, diagnosis, sites, deck=None, gate=None):
        self.label, self.quality, self.order = label, quality, order
        self.death_floor, self.diagnosis, self.sites = death_floor, diagnosis, sites
        self.exhausted = False
        self.picks = 0
        self.deck = deck            # Counter of card ids at the end of the trajectory (cluster cap, family record)
        self.gate = gate            # boss gate the trajectory still has to pass (family record only)
        self.memo = [0, []]         # incremental scan of that gate's entries (GateModels.family)
        self.carried = 0.0          # HP share carried into the fatal fight when it is a later boss of its act (carry)
        self.act = 0                # act of the fatal floor (stall)
        self.f1_entry = None        # exact full entering prefix, only for a fatal final F1 (joint focus)
        self.retention_key = None


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
    stall       number of distinct entries from which a boss gate without a
                pass counts as stalled (0 = off). Only the furthest gate
                reached can be stalled, and only when it is the first boss of
                a later act. While it is, the focus picks of the trajectories
                that died in its act alternate between the usual pick and the
                cheapest pending alternative of the act before. The usual
                cost grows by one per floor back and every new elite brings
                cheap alternatives next to the gate, so without the switch
                the focus share stays in the act of the gate however often
                the gate fails. Retained runs (iteration-057, a split made
                after the pre-registered pooled test had failed): from 32
                failed entries on, deviations one act back passed such a gate
                2.9 times as often per evaluation as deviations in its act
                (1.27 times per action); at the second boss of the final act
                it was the other way round, so that gate never triggers. The
                trigger is a count of attempts, not time or nodes. Nothing is
                removed: without a pending alternative in the earlier act the
                usual pick is made.
    stall_extended keeps the same threshold and focus/explore allocation,
                adds per-lineage second-act boss/F1 stalls, and prefers previously unseen
                pre-act3 decision stems on every other focus pick. Third-act
                work of a paused lineage remains unresolved; the usual half
                uses unpaused sources or earlier decisions. An act 2 boss
                reached from the backoff branches must itself have `stall`
                distinct failed entries before allocation steps back to act 1.
                Default off. Neither corridor deaths nor a new proposal are
                boss entries or evidence of progress.
                F2 never triggers a pause or backoff unless stall_f2 is also on.
    stall_f2    independently enable F2 lineage backoff for A/B comparisons;
                requires stall_extended and a positive existing stall threshold.
                Distinct full F2 entry prefixes count; retrying one entry with
                another search budget is not a new entry. Default off.
    f2_joint_focus alternate the ordinary elite order with final-F1 elites
                ranked by full-HP F2 readiness. Only equal death floors move;
                pool retention, cluster limits and rank shares remain intact.
                Cannot be combined with carry. Default off.
    """

    def __init__(self, scope_prefix=None, *, models=None, elites: int = 8, pool: int = 48,
                 focus: int = 3, explore: int = 1, site_cap: int | None = 12,
                 cluster_cap: int = 0, cluster_percent: int = 75, family: bool = False, optimism: int = 0,
                 carry: bool = False, stall: int = 0, stall_extended: bool = False, stall_f2: bool = False,
                 f2_joint_focus: bool = False, plateau_diversity: bool = False,
                 recover_deferred: bool = False):
        if min(elites, pool, focus) < 1 or explore < 0 or pool < elites:
            raise ValueError('invalid focus scheduler configuration')
        if type(cluster_cap) is not int or cluster_cap < 0 or type(cluster_percent) is not int or not 1 <= cluster_percent <= 100:
            raise ValueError('invalid deck cluster configuration')
        if type(family) is not bool or type(optimism) is not int or not 0 <= optimism <= 400 or ((family or optimism) and models is None):
            raise ValueError('invalid family record configuration')
        if type(carry) is not bool:
            raise ValueError('invalid carried HP configuration')
        if type(stall) is not int or stall < 0:
            raise ValueError('invalid stall configuration')
        if type(stall_extended) is not bool:
            raise ValueError('invalid lineage backoff configuration')
        if type(stall_f2) is not bool or (stall_f2 and (not stall_extended or stall <= 0)):
            raise ValueError('F2 backoff requires extended stall and a positive threshold')
        if type(f2_joint_focus) is not bool or (f2_joint_focus and carry):
            raise ValueError('joint F2 focus cannot be combined with carried HP focus')
        if type(plateau_diversity) is not bool:raise ValueError('plateau_diversity must be boolean')
        self.plateau_diversity = plateau_diversity
        self.retained_neighborhoods = set()
        self.plateau_admissions = 0
        self.neighborhood_improvements = 0
        self.neighborhood_duplicates = 0
        self.explorer = IndexedStrategicScheduler(scope_prefix, site_cap, recover_deferred=recover_deferred)
        self.scope_length = len(scope_prefix or [])
        self.models = models
        self.elites, self.pool_size = elites, pool
        self.focus_share, self.explore_share = focus, explore
        self.cluster_cap, self.cluster_percent = cluster_cap, cluster_percent
        self.family, self.optimism = family, optimism
        self.carry = carry
        self.f2_joint_focus = f2_joint_focus
        self.f1_entries = {}        # label -> exact canonical full prefix entering F1
        self.f2_readiness = {}      # full entering prefix -> status/mean; allocation only
        self.joint_place_changes = 0
        self.joint_compared = 0
        self.stall = stall
        self.lineage = LineageBackoff(stall, enabled=stall_extended, f2=stall_f2,
                                     prefixes=self.explorer.prefixes)
        self.extended_back = 0
        self.extended_plain = 0
        self.gates = {}             # (act, ordinal) -> [entry nodes, passes] over every absorbed result (stall)
        self.stall_back = 0         # focus picks made while stalled: sent one act back / usual
        self.stall_plain = 0
        self.crowded_out = 0        # trajectories refused or evicted because their deck family was full
        self.pool = []              # sorted [(negated quality, order, label)]
        self.sources = {}           # label -> _Source (pool members only)
        self.qualities = set()      # quality tuples already represented in the pool
        self.order = 0
        self.focus_picks = 0
        self.explore_picks = 0
        self.promotions = 0

    # ---- intake ---------------------------------------------------------
    def add(self, result, label, spec=None, *, completed_wall_seconds=None):
        self.explorer.add(result, label)
        fatal_f1 = None
        if self.f2_joint_focus and not result.get('synthetic'):
            rows = boss_fight_rows(result)
            row = next((row for row in rows if tuple(row['gate']) == (2, 0)), None)
            if row is not None:
                entry = canonical(result['trace'][:row['index']])
                self.f1_entries[label] = entry
                if row['lost'] is not None:
                    fatal_f1 = entry
        if self.stall or self.lineage.enabled:
            self.lineage.observe(result, label, spec, completed_wall_seconds=completed_wall_seconds)
        if self.stall and result.get('trace'):
            self._record_gates(result)
        if classify_failure(result) != 'NATIVE_ROUTE_DEATH' or not result.get('trace'):
            return
        quality = utility(result)
        # Legacy approximation: retain only one trajectory per outcome tuple.
        # i085 checks actual decision neighborhoods instead; equal utility alone
        # does not establish that two macro neighborhoods are interchangeable.
        if not self.plateau_diversity and quality in self.qualities:
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
        # Only identical retained decision neighborhoods are redundant for
        # i085 focus allocation. These are exact alternative-prefix trie IDs,
        # not a deck/HP feature signature or an exact game-state assertion.
        retention = (tuple((i, tuple(sorted(key for key, _, _ in alternatives)))
                           for i, _, _, _, alternatives in sites) if self.plateau_diversity else None)
        if self.plateau_diversity and retention in self.retained_neighborhoods:
            previous = next(s for s in self.sources.values() if s.retention_key == retention)
            if quality <= previous.quality:
                self.neighborhood_duplicates += 1
                return
            # A stronger combat result can have precisely the same preceding
            # macro alternatives. Keep the improved source, not the first one.
            self.sources.pop(previous.label)
            self.pool.remove((tuple(-x for x in previous.quality), previous.order, previous.label))
            self.retained_neighborhoods.discard(retention)
            self.qualities = {s.quality for s in self.sources.values()}
            self.neighborhood_improvements += 1
            victim = None  # replacing the old source already freed one place
        self.plateau_admissions += int(self.plateau_diversity and quality in self.qualities)
        self.order += 1
        gate = None
        if self.family:
            # Dead in a boss fight: that gate. Dead elsewhere: the next boss of the act.
            act = int((result.get('observation') or {}).get('act') or 0)
            gate = (act, sum(row['gate'][0] == act and row['lost'] is None for row in boss_fight_rows(result)))
        source = _Source(label, quality, self.order, death_floor, FailureAnalyzer.analyze(result), sites, deck, gate)
        source.retention_key = retention
        if self.plateau_diversity:self.retained_neighborhoods.add(retention)
        source.act = int((result.get('observation') or {}).get('act') or 0)
        source.f1_entry = fatal_f1
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
            self.retained_neighborhoods.discard(dropped.retention_key)
            self.pool.remove((tuple(-x for x in dropped.quality), dropped.order, victim))
        while len(self.pool) > self.pool_size:
            _, _, dropped = self.pool.pop()
            source = self.sources.pop(dropped)
            self.qualities.discard(source.quality)
            self.retained_neighborhoods.discard(source.retention_key)
        if self.plateau_diversity:
            self.qualities = {source.quality for source in self.sources.values()}

    # ---- stalled gate ---------------------------------------------------
    def _record_gates(self, result):
        """Distinct entries and passes of every boss gate, over every absorbed
        result (also the ones that are no focus source). An entry is the exact
        prefix the fight was entered with: a retry of the same entry adds no
        entry, only a pass if it wins."""
        fights = boss_fight_rows(result)
        if not fights:
            return
        path = self.explorer.prefixes.index(result['trace'])
        for row in fights:
            record = self.gates.setdefault(tuple(row['gate']), [set(), 0])
            record[0].add(path[row['index']])
            record[1] += int(row['lost'] is None)

    def _stalled(self):
        """Act the focus picks step back to, or None. Stalled: the furthest
        boss gate reached is the first boss of a later act, has `stall`
        distinct entries or more and no pass."""
        if not self.gates:
            return None
        gate = max(self.gates)
        entries, passes = self.gates[gate]
        if gate[1] != 0 or gate[0] < 1 or passes or len(entries) < self.stall:
            return None
        return gate[0] - 1

    def preparation_blocked(self, source_or_spec):
        """Whether to defer this source's preparation proposals.

        The coordinator must leave blocked items pending. The historical
        first-boss stall still alternates its focus choices unchanged; this
        hook prevents its preparation queue from bypassing that backoff.
        """
        if self.lineage.enabled:
            # Extended mode owns its pause/resume epoch. Falling back to the
            # historical global gate totals would immediately re-block an old
            # lineage after a new lineage has legitimately resumed it.
            return self.lineage.preparation_blocked(source_or_spec)
        back = self._stalled() if self.stall else None
        source = self.lineage.labels.get(self.lineage._source(source_or_spec))
        return bool(back is not None and source is not None and
                    source.terminal_act is not None and source.terminal_act > back)

    def dispatch_blocked(self, spec):
        """A reversible allocation pause, never an infeasibility filter."""
        return self.lineage.dispatch_blocked(spec)

    def has_blocked_pending(self):
        return any(self.dispatch_blocked({'group': {'source': branch.source_label,
                                                    'act': branch.act, 'index': branch.index}})
                   for branch in self.explorer.branches.values()) if self.lineage.active else False

    def _backoff_next(self):
        """Prioritise existing pre-boss branches of the requested earlier act.

        Keep the existing explorer priority inside each novelty class. No
        model score or share is introduced. Neither late blocked branches nor
        uncertain boss boundaries are taken or deleted. No suitable proposal
        returns None so the coordinator can use its existing fresh sources.
        """
        target = self.lineage.target_act()
        if target is None:
            return None
        best = None
        for key, branch in self.explorer.branches.items():
            if branch.act != target or not self.lineage.candidate_before_boss(branch):
                continue
            if self.dispatch_blocked({'group': {'source': branch.source_label,
                                                'act': branch.act, 'index': branch.index}}):
                continue
            action = json.loads(self.explorer.prefixes.actions[key])
            priority = (self.lineage.candidate_seen(branch, action),
                        self.explorer._priority(branch, branch.category), branch.order)
            if best is None or priority < best[0]:
                best = priority, branch
        if best is None:
            return None
        branch = best[1]
        self.explorer.take(branch.key)
        self.focus_picks += 1
        source = self.sources.get(branch.source_label)
        if source is not None:
            source.picks += 1
        return {'prefix': self.explorer.prefixes.restore(branch.key), 'category': 'focus',
                'phase': branch.phase, 'floor': branch.floor, 'act': branch.act,
                'index': branch.index, 'source': branch.source_label, 'diagnosis': branch.diagnosis,
                'step_back': True, 'lineage_backoff': True, 'target_act': target}

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
        if self.f2_joint_focus:
            alternate = self._joint_places(labels)
            if alternate == labels:
                return labels
            return self._alternate_places(labels, alternate)
        if not self.carry:
            return labels

        def key(label):
            source = self.sources[label]
            # Act and floor first: carried HP only orders trajectories that died in the same fight.
            return tuple(-x for x in source.quality[:4]) + (-source.carried,) + tuple(-x for x in source.quality[4:]), source.order
        return self._alternate_places(labels, sorted(labels, key=key))

    @staticmethod
    def _alternate_places(labels, alternate):
        merged, taken = [], set()
        for pair in zip(labels, alternate):
            for label in pair:
                if label not in taken:
                    taken.add(label)
                    merged.append(label)
        return merged

    def record_f2_readiness(self, source_label, status, *, mean=None, entry_key=None):
        """Bind a sampler signal to the source's actual full F1 entry bytes.

        The sampler enforces the five/ten-sample validity contract. This
        consumer never accepts a projected deck identity as a real entry key.
        Sources sharing measured samples still retain their own exact keys.
        """
        if not self.f2_joint_focus:
            return False
        known = self.f1_entries.get(source_label)
        if known is None or (entry_key is not None and entry_key != known):
            return False
        if status not in ('VIABLE', 'PENDING', 'UNKNOWN', 'DEAD_AT_FULL_HP'):
            return False
        if type(mean) not in (int, float) or not math.isfinite(mean) or mean < 0:
            mean = None
        self.f2_readiness[known] = {'status': status, 'mean': mean}
        return True

    def f1_entry_key(self, source_label):
        """Exact entering-prefix identity for sampler/retry integration."""
        return self.f1_entries.get(source_label)

    def _joint_places(self, labels):
        alternate = list(labels)
        groups = {}
        for index, label in enumerate(labels):
            source = self.sources[label]
            if source.f1_entry is not None:
                groups.setdefault((source.act, source.death_floor), []).append(index)
        # Sorting only these positions keeps every other death location
        # fixed in each order, not merely close in a coarse feature bin.
        for positions in groups.values():
            def key(index):
                source = self.sources[labels[index]]
                state = self.f2_readiness.get(source.f1_entry) or {'status': 'UNKNOWN', 'mean': None}
                status, mean = state['status'], state.get('mean')
                rank = 0 if status == 'VIABLE' else 2 if status == 'DEAD_AT_FULL_HP' else 1
                return (rank, mean is None if status == 'VIABLE' else False,
                        -mean if status == 'VIABLE' and mean is not None else 0.0, index)
            for position, candidate in zip(positions, sorted(positions, key=key)):
                alternate[position] = labels[candidate]
        return alternate

    def _elite_sources(self, places=None):
        elites = []
        labels = self._places() if places is None else places
        if not self.cluster_cap:
            for label in labels:
                source = self.sources[label]
                if source.exhausted:
                    continue
                elites.append(source)
                if len(elites) == self.elites:
                    break
            return elites
        decks = []
        for label in labels:
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

    def _candidate(self, source, only_act=None):
        """Cheapest pending alternative of one trajectory. Cost counts floors
        back from the fatal floor plus two for every option of the same
        decision already tried or ranked better, shifted by the option's
        learned value: the search widens a late decision and walks back to
        earlier floors at the same time. `only_act` keeps the decisions of
        that act alone (stall); the order among them is the same."""
        best = None
        pending = self.explorer.pending
        for index, floor, act, phase, alternatives in source.sites:
            if only_act is not None and act != only_act:
                continue
            if self.lineage.active and self.dispatch_blocked({'group': {'source': source.label, 'act': act, 'index': index}}):
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
            row = (back + 2 * tried + penalty - 2.0 * value, -index, position, key, index, floor, act, phase)
            if best is None or row < best:
                best = row
        return best

    def _focus(self):
        extended_target = self.lineage.target_act()
        if extended_target is not None and self.extended_back < self.extended_plain:
            group = self._backoff_next()
            if group is not None:
                self.extended_back += 1
                return group
        elites = self._elite_sources()
        # Rank-weighted fair share: the trajectory furthest below its share goes next.
        weights = [1.0 / (rank + 1) for rank in range(len(elites))]
        if self.family:
            weights = [weight * self.models.optimistic(*self._record(source)) for weight, source in zip(weights, elites)]
        total = sum(weights)
        ranked = sorted(range(len(elites)), key=lambda rank: (
            elites[rank].picks - (self.focus_picks + 1) * weights[rank] / total, rank))
        back = self._stalled() if self.stall and extended_target is None else None
        baseline_key = self._baseline_focus_key(back) if self.f2_joint_focus else None
        for rank in ranked:
            source = elites[rank]
            # Stalled gate: trajectories that died in its act take turns between the usual pick and the act before.
            turns = back is not None and source.act > back
            candidate = self._candidate(source, back) if turns and self.stall_back < self.stall_plain else None
            stepped = candidate is not None
            if candidate is None:
                candidate = self._candidate(source)
            if candidate is None:
                # A temporarily blocked source must become usable on resume.
                # Only genuine exhaustion removes its elite place.
                if not self.lineage.active or not any(
                    self.explorer.pending(key) for _, _, _, _, alternatives in source.sites
                    for key, _, _ in alternatives):
                    source.exhausted = True
                continue
            _, _, _, key, index, floor, _, phase = candidate
            self.explorer.take(key)
            if self.f2_joint_focus:
                self.joint_compared += 1
                self.joint_place_changes += int(baseline_key is not None and key != baseline_key)
            source.picks += 1
            self.focus_picks += 1
            group = {'prefix': self.explorer.prefixes.restore(key), 'category': 'focus', 'phase': phase, 'floor': floor,
                     'index': index, 'source': source.label, 'diagnosis': source.diagnosis, 'elite_rank': rank}
            if turns:
                self.stall_back += int(stepped)
                self.stall_plain += int(not stepped)
                if stepped:
                    group['step_back'] = True
            if extended_target is not None:
                self.extended_plain += 1
            return group
        if extended_target is not None:
            group = self._backoff_next()
            if group is not None:
                self.extended_back += 1
            return group
        return None

    def _baseline_focus_key(self, back):
        """The next normal-focus key without the joint elite order, read only.

        This is a one-decision counterfactual in the current scheduler state,
        not an estimate of an alternate run or an outcome improvement.
        """
        elites = self._elite_sources([label for _, _, label in self.pool])
        weights = [1.0 / (rank + 1) for rank in range(len(elites))]
        if self.family:
            weights = [weight * self.models.optimistic(*self._record(source)) for weight, source in zip(weights, elites)]
        total = sum(weights)
        ranked = sorted(range(len(elites)), key=lambda rank: (
            elites[rank].picks - (self.focus_picks + 1) * weights[rank] / total, rank))
        for rank in ranked:
            source = elites[rank]
            turns = back is not None and source.act > back
            candidate = self._candidate(source, back) if turns and self.stall_back < self.stall_plain else None
            if candidate is None:
                candidate = self._candidate(source)
            if candidate is not None:
                return candidate[3]
        return None

    def _explore(self):
        blocked = self.lineage.target_act() is not None
        if blocked and self.explorer.recover_deferred:self.explorer.promote_deferred()
        group = self._explore_unblocked() if blocked else self.explorer.next()
        if group is not None:
            self.explore_picks += 1
        return group

    def _explore_unblocked(self):
        """Original weighted heap order, with paused items left in the heaps."""
        explorer = self.explorer
        total = sum(explorer.counts.values()) + 1
        for category in sorted(explorer.weights, key=lambda c: explorer.weights[c] * total - explorer.counts[c], reverse=True):
            heap = explorer.heaps[category]
            deferred = []
            try:
                while heap:
                    old, key = heapq.heappop(heap)
                    branch = explorer.branches.get(key)
                    if branch is None:
                        continue
                    if self.dispatch_blocked({'group': {'source': branch.source_label,
                                                        'act': branch.act, 'index': branch.index}}):
                        deferred.append((old, key))
                        continue
                    current = explorer._priority(branch, category)
                    if current != old:
                        if current < old:
                            raise AssertionError('Priority decreased; lazy heap proof no longer valid')
                        heapq.heappush(heap, (current, key))
                        continue
                    explorer.branches.pop(key)
                    explorer.submitted.add(key)
                    explorer.counts[category] += 1
                    explorer.last_key = key
                    explorer.source_visits[branch.source_label] += 1
                    explorer.room_visits[(branch.act, branch.floor, branch.phase)] += 1
                    return {'prefix': explorer.prefixes.restore(key), 'category': category,
                            'phase': branch.phase, 'floor': branch.floor, 'index': branch.index,
                            'source': branch.source_label, 'diagnosis': branch.diagnosis}
            finally:
                for item in deferred:
                    heapq.heappush(heap, item)
        return None

    def next(self):
        focus_first = self.explore_share == 0 or self.focus_picks * self.explore_share <= self.explore_picks * self.focus_share
        if focus_first:
            return self._focus() or self._explore()
        return self._explore() or self._focus()

    def snapshot(self):
        report = self._snapshot()
        if self.plateau_diversity:
            report['macro_plateaus'] = {'enabled':True, 'equal_quality_admissions':self.plateau_admissions,
                'same_neighborhood_improvements':self.neighborhood_improvements,
                'identical_neighborhoods_skipped':self.neighborhood_duplicates,
                'retained_neighborhoods':len(self.retained_neighborhoods),
                'scope':'focus allocation only; explorer keeps its exact-prefix alternatives'}
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
        if self.stall:
            furthest = max(self.gates) if self.gates else None
            report.update(focus_stall={'threshold_entries': self.stall, 'furthest_gate': list(furthest) if furthest else None,
                                       'step_back_act': self._stalled(), 'step_back_evaluations': self.stall_back,
                                       'usual_evaluations_while_stalled': self.stall_plain,
                                       'gates': [{'gate': list(gate), 'entries': len(self.gates[gate][0]), 'passes': self.gates[gate][1]}
                                                 for gate in sorted(self.gates)]},
                          stalled_gate_is='first boss of a later act with that many distinct entries and no pass; focus picks of '
                                          'the trajectories that died in its act alternate with the act before; allocation only, never a bound')
        if self.lineage.enabled:
            report['focus_stall_extended'] = {**self.lineage.snapshot(),
                                             'step_back_evaluations': self.extended_back,
                                             'usual_evaluations_while_stalled': self.extended_plain,
                                             'original_focus_explore_ratio_preserved': True}
        if self.f2_joint_focus:
            report['f2_joint_focus'] = {
                'enabled': True, 'focus_allocations_compared': self.joint_compared,
                'dispatch_order_changes': self.joint_place_changes,
                'count_is': 'normal focus key differs from ordinary order in the current scheduler state; not a route improvement claim',
                'elites': [{'label': source.label, **(self.f2_readiness.get(source.f1_entry) or
                            {'status': 'UNKNOWN', 'mean': None}), 'final_f1_death': source.f1_entry is not None}
                           for source in self._elite_sources()],
                'allocation_only': True, 'pruning': False}
        return report

    def _snapshot(self):
        if not self.cluster_cap:
            elites = self._elite_sources() if self.f2_joint_focus else [self.sources[label] for _, _, label in self.pool[:self.elites]]
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
