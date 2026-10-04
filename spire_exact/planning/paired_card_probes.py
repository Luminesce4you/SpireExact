"""Paired synthetic card probes (iteration-068): ordering signals, never proofs.

Real final-act first-boss entries supply replayable prefixes. In disposable
workers, each base / add-card arm plays the second boss at full HP with five
shared deterministic RNG samples. Only a fully paired table changes third-act
card tiers. No synthetic state belongs to a real trajectory or result cache.
"""
from __future__ import annotations
from collections import Counter
from copy import deepcopy
import hashlib
import math
from ..canonical import canonical
from .probes import gate_entries, probe_request, probe_outcome, scaled

SAMPLES = 5
CARDS = 5
FIRST_THRESHOLD = 1
SECOND_THRESHOLD = 32
THRESHOLD_FACTOR = 4
MERGES = ('latest', 'mean')
E45 = {'gate_plans': {'Boss': {'members': [
    {'mode': 'Evaluate', 'beam': 45, 'nodes': 120000}], 'select': 'auto'}}}
SCOPE = ('synthetic; third-act card_reward paired:card:/paired:card_skip ordering only; never legality, '
         'pruning, a real trajectory, checkpoint, cache, bound or proof')
MAP_OBSERVATION_KEYS = frozenset({'act','floor','room','hp','max_hp','gold','deck','relics',
    'strategic','selection','hand','turn','energy','block','enemies','potions','character','rng','score'})


def candidates(result: dict, until: int, act: int) -> list[str]:
    """Actual offered labels of this trajectory: current act first, earlier
    offers fill missing slots. Frequency descending, then card ID ascending.
    Never propose a card which was absent from the retained native menus."""
    current, earlier = Counter(), Counter()
    for row in (result.get('decision_evidence') or [])[:until]:
        owner = current if (row.get('observation') or {}).get('act') == act else earlier
        for labels in row.get('option_labels') or []:
            for label in labels:
                if isinstance(label, str) and label.startswith('card:') and label[5:]:
                    owner[label[5:]] += 1
    order = [card for card, _ in sorted(current.items(), key=lambda x: (-x[1], x[0]))]
    order += [card for card, _ in sorted(earlier.items(), key=lambda x: (-x[1], x[0])) if card not in order]
    return order[:CARDS]


def rng_samples(solver_seed: int, table: int) -> tuple[int, ...]:
    """Independent combat samples; the same set is shared by every arm."""
    values = []
    for index in range(SAMPLES):
        value = int.from_bytes(hashlib.sha256(
            f'paired-card/v1:{solver_seed}:{table}:{index}'.encode()).digest()[:8], 'big')
        while value in values:
            value = (value + 1) % (1 << 64)
        values.append(value)
    return tuple(values)


def valid_outcome(outcome) -> bool:
    if not isinstance(outcome, dict) or type(outcome.get('won')) is not bool:
        return False
    if outcome['won']:
        score = outcome.get('outcome')
        return type(score) in (int, float) and math.isfinite(score) and score >= 1
    lost = outcome.get('lost')
    return (isinstance(lost, (list, tuple)) and len(lost) == 2
            and all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in lost)
            and lost[1] > 0 and lost[0] <= lost[1])


def paired_outcome(decision, request) -> dict | None:
    """Reduce only the requested, fully replayed synthetic arm. Missing fatal
    progress is UNKNOWN, even if the host finished the fight. Keep the legacy
    single-sample gate-probe interpretation independent of this stricter path."""
    if (not isinstance(decision, dict) or decision.get('reason') is not None
            or decision.get('native_terminal_observed') is not False or decision.get('value') is not None):
        return None
    info = decision.get('probe') or {}
    history = request.get('history') or []
    if (info.get('entered') is not True or info.get('fought') is not True
            or decision.get('consumed') != len(history) or info.get('entry_index') != len(history)):
        return None
    try:
        if canonical(info.get('edits') or []) != canonical((request.get('probe') or {}).get('edits') or []):
            return None
        outcome = probe_outcome(decision)
    except (ValueError,TypeError,KeyError,IndexError):
        return None
    return outcome if valid_outcome(outcome) else None


def probe_workload(rows: list, expected: int) -> dict:
    """Known work from every returned raw decision, including rejected arms.
    Missing worker/metric reports remain explicit unknowns; a timeout never
    certifies zero spent nodes. Search sums are measured subsets in that case.
    """
    nodes, search_us, native_us, unknown = 0, 0, 0, 0
    for index in range(max(expected,len(rows))):
        decision = rows[index][0] if index < len(rows) else None
        metrics = decision.get('advisor_metrics') if isinstance(decision,dict) else None
        performance = decision.get('performance') if isinstance(decision,dict) else None
        searches = metrics.get('searches') if isinstance(metrics,dict) else None
        native = performance.get('wall_us') if isinstance(performance,dict) else None
        known = isinstance(searches,list) and type(native) is int and native >= 0
        if type(native) is int and native >= 0:
            native_us += native
        if isinstance(searches,list):
            for search in searches:
                if not isinstance(search,dict):
                    known = False
                    continue
                count, wall = search.get('expanded_nodes'), search.get('wall_us')
                if type(count) is int and count >= 0:
                    nodes += count
                else:known = False
                if type(wall) is int and wall >= 0:
                    search_us += wall
                else:known = False
        unknown += not known
    return {'measured_expanded_combat_nodes':nodes, 'measured_probe_search_seconds':search_us / 1e6,
        'measured_probe_native_seconds':native_us / 1e6, 'unknown_work_probes':unknown,
        'work_measurement_complete':unknown == 0}


class PairedCardProbes:
    """One run's private synthetic evidence. Entry equality uses full bytes,
    bound to this run and the template's native initial context. Sampling
    thresholds are 1, 32, 128, 512, ... distinct real entries; no clock gates.

    i082 options, all off by default: every=N samples at 1, N, 2N, ...
    entries; dedup skips a table whose deck, relics and candidates equal an
    earlier table's (the threshold stays due); merge='mean' averages each
    label's paired gain over complete tables instead of keeping the latest.
    """
    def __init__(self, enabled: bool, solver_seed: int, *, context=None, inputs=None,
                 inactive_reason: str | None = None, every: int = 0, dedup: bool = False,
                 merge: str = 'latest'):
        if type(every) is not int or every < 0 or type(dedup) is not bool or merge not in MERGES:
            raise ValueError('invalid paired card probe schedule')
        self.every, self.dedup, self.merge = every, dedup, merge
        self.table_keys = set()
        self.duplicate_tables = 0
        self.gain_sums = {}
        self.joint_rows_built = set()
        self.joint_skipped = set()
        self.enabled = enabled
        self.solver_seed = solver_seed
        self.context = deepcopy(context)
        self.inputs = deepcopy(inputs)
        self.inactive_reason = 'disabled' if not enabled else inactive_reason
        self.metadata_seen = False
        self.seen = set()
        self.tables = []
        self.next_threshold = FIRST_THRESHOLD
        self.completed_batches = 0
        self.attempted_probes = 0
        self.usable_probes = 0
        self.errors = 0
        self.nodes = 0
        self.search_seconds = 0.0
        self.native_seconds = 0.0
        self.unknown_work_probes = 0
        self.signals = {}

    def observe(self, result: dict, label: str, searched: int, template: dict) -> list[dict]:
        if not self.enabled or self.inactive_reason:
            return []
        if (template.get('character') != 'IRONCLAD' or type(template.get('ascension')) is not int
                or template['ascension'] != 10 or template.get('unlocks') != 'all'):
            self.inactive_reason = 'initial_context_outside_ironclad_a10_all_scope'
            return []
        if not template.get('advisor'):
            self.inactive_reason = 'no_combat_advisor'
            return []
        campaign = result.get('campaign') or {}
        act_count, bosses = campaign.get('act_count'), campaign.get('final_act_boss_count')
        if type(act_count) is not int or type(bosses) is not int:
            return []                     # old archives / missing metadata are unknown
        self.metadata_seen = True
        if act_count != 3 or bosses < 2:
            self.inactive_reason = 'campaign_outside_three_act_double_boss_scope'
            return []
        specs = []
        for entry in gate_entries(result):
            obs = entry['entry']
            act = obs.get('act')
            if (act != act_count - 1
                    or entry['gate'][1] != 0 or entry['index'] < searched):
                continue                 # this feature's validated scope is third-act F1
            prefix = result['trace'][:entry['enter']]
            evidence = result.get('decision_evidence') or []
            map_row = evidence[entry['enter']] if entry['enter'] < len(evidence) else {}
            before = map_row.get('observation') or {}
            if (map_row.get('phase') != 'map' or not isinstance(before,dict)
                    or not MAP_OBSERVATION_KEYS <= set(before) or not isinstance(before.get('rng'),dict)
                    or not isinstance(before.get('deck'),list) or before.get('turn') is not None):
                continue                 # no complete native map state to bind
            key = canonical({'context': self.context, 'inputs': self.inputs,
                'native_context': {k: template.get(k) for k in ('seed','character','ascension','unlocks')},
                'history': result['trace'][:entry['index']]})
            if key in self.seen:
                continue
            self.seen.add(key)
            if len(self.seen) < self.next_threshold:
                continue
            cards = candidates(result, entry['enter'], act)
            # Synthetic F2 starts at the map boundary, before any F1 start hook
            # can raise max HP; requesting combat-entry HP could be illegal.
            hp = before.get('max_hp')
            try:
                full_hp = int(hp)
                if full_hp < 1 or float(hp) != full_hp:
                    continue
            except (TypeError, ValueError, OverflowError):
                continue
            if not cards:
                continue
            content = self._content(before, cards) if self.dedup else None
            if content is not None and content in self.table_keys:
                self.duplicate_tables += 1
                continue                 # same probes as an earlier table; the threshold stays due
            table = len(self.tables)
            samples = rng_samples(self.solver_seed, table)
            names = ['card_skip'] + ['card:' + card for card in cards]
            state = {'table': table, 'base': label, 'gate': list(entry['gate']), 'entry_index': entry['index'],
                'threshold': self.next_threshold, 'entry_count': len(self.seen),
                'labels': names, 'samples': list(samples), 'rows': {}, 'status': 'UNKNOWN',
                'complete_paired_samples': 0, 'means': {}, 'paired_gains': {},
                'errors': 0, 'full_hp': full_hp}
            self.tables.append(state)
            if content is not None:
                self.table_keys.add(content)
            self.next_threshold = (self.every * len(self.tables) if self.every else
                                   SECOND_THRESHOLD if table == 0 else self.next_threshold * THRESHOLD_FACTOR)
            for sample in samples:
                requests = []
                for card in [None, *cards]:
                    edits = [] if card is None else [{'op':'add', 'card':card, 'upgrade':0}]
                    request = probe_request(template, result['trace'], entry, edits,
                                            hp=full_hp, rng=sample, advisor_patch=E45)
                    request['probe']['enter'] = {'kind':'encounter', 'boss':1}
                    request['probe']['expected_entry_observation'] = deepcopy(before)
                    requests.append(request)
                specs.append({'kind':'paired_card_probe', 'category':'probe', 'gate':entry['gate'],
                    'table':table, 'sample':sample, 'base':label, 'labels':list(names),
                    'requests':requests, 'request':{'history':deepcopy(prefix), 'generate_candidate':False}})
        return specs

    def absorb(self, spec: dict, outcomes: list, workload: dict | None = None) -> None:
        """Every arm must finish for every requested sample. Partial tables
        remain UNKNOWN and do not overwrite a previous complete signal."""
        table = self.tables[spec['table']]
        sample = spec['sample']
        if sample not in table['samples'] or sample in table['rows']:
            raise ValueError('unexpected or duplicate paired probe sample')
        if spec['labels'] != table['labels']:
            raise ValueError('paired probe arms changed')
        self.completed_batches += 1
        self.attempted_probes += len(table['labels'])
        self.usable_probes += sum(valid_outcome(row) for row in outcomes)
        errors = sum(not valid_outcome(row) for row in outcomes) + max(0,len(table['labels']) - len(outcomes))
        table['errors'] += errors
        self.errors += errors
        if workload is None:
            # Reduced outcomes cannot account for failed/no-return arms or
            # native wall time. Keep their partial cost as measured, UNKNOWN.
            workload = {'measured_expanded_combat_nodes':sum(int(row.get('nodes')or 0) for row in outcomes if valid_outcome(row)),
                'measured_probe_search_seconds':sum(float(row.get('search_seconds')or 0) for row in outcomes if valid_outcome(row)),
                'measured_probe_native_seconds':0.0, 'unknown_work_probes':len(table['labels'])}
        self.nodes += workload['measured_expanded_combat_nodes']
        self.search_seconds += workload['measured_probe_search_seconds']
        self.native_seconds += workload['measured_probe_native_seconds']
        self.unknown_work_probes += workload['unknown_work_probes']
        complete = len(outcomes) == len(table['labels']) and all(valid_outcome(row) for row in outcomes)
        table['rows'][sample] = deepcopy(outcomes) if complete else None
        table['complete_paired_samples'] = sum(rows is not None for rows in table['rows'].values())
        if len(table['rows']) != SAMPLES or table['complete_paired_samples'] != SAMPLES:
            return
        rows = [table['rows'][s] for s in table['samples']]
        total = max([row['lost'][1] for group in rows for row in group if not row['won']] or [1.0])
        means = {name:sum(scaled(group[i], total) for group in rows) / SAMPLES
                 for i,name in enumerate(table['labels'])}
        gains = {name:mean - means['card_skip'] for name,mean in means.items()}
        table.update(status='PAIRED_SIGNAL', life_total=total, means=means, paired_gains=gains)
        # Latest completed table for a label, deterministic in absorption order.
        # Transfer across entry decks is an unvalidated rollout heuristic,
        # not a causal card value and never a feasibility / dominance cut.
        # No synthetic outcome is sent to GateModels.add/add_probes.
        if self.merge == 'mean':
            # i082: every complete table weighs one; partial tables never count.
            for name, gain in gains.items():
                summed, count = self.gain_sums.get(name, (0.0, 0))
                self.gain_sums[name] = (summed + gain, count + 1)
                self.signals[name] = (summed + gain) / (count + 1)
        else:
            self.signals.update(gains)

    @staticmethod
    def _content(before: dict, cards: list):
        """What a table would measure: deck (id, upgrade) multiset, relics and
        candidate cards. It only avoids spending the same probes twice; it is
        not a state identity and never prunes anything."""
        try:
            deck = sorted([str(card['id']), int(card.get('upgrade') or 0)] for card in before['deck'])
            relics = sorted(str(relic) for relic in before.get('relics') or [])
            return canonical({'deck': deck, 'relics': relics, 'cards': sorted(cards)})
        except (TypeError, KeyError, ValueError, AttributeError):
            return None

    def joint_rows(self, base_for_label) -> list[dict]:
        """i082: the add-card arms of every complete table as extra rows of the
        separate F1-real + full-HP F2 joint regression (F2JointModel).

        The F2 part is the arm's own five shared samples. The card is added at
        the F1 door and the probe skips F1, so the F1 part is borrowed from the
        real base entry: a card's effect on F1 is unknown and counted as none.
        The base itself is the readiness module's own row. Synthetic allocation
        evidence only, never a real gate row, bound, cut or proof.
        """
        rows = []
        for table in self.tables:
            if table['status'] != 'PAIRED_SIGNAL':
                continue
            base = base_for_label(table['base'])
            observation = (base or {}).get('observation')
            if (not base or base.get('f1_raw') is None or not isinstance(base.get('rawentrykey'), bytes)
                    or not isinstance(observation, dict) or not isinstance(observation.get('deck'), list)
                    or not any(isinstance(source, dict) and source.get('label') == table['base']
                               and source.get('index') == table.get('entry_index') for source in base.get('sources') or [])):
                self.joint_skipped.add(table['table'])
                continue
            self.joint_skipped.discard(table['table'])
            for index, name in enumerate(table['labels'][1:], 1):
                arm = deepcopy(observation)
                arm['deck'] = [*arm['deck'], {'id': name[5:], 'upgrade': 0}]
                key = base['rawentrykey'] + b'\x00paired-card/v1:' + name.encode()
                self.joint_rows_built.add(key)
                rows.append({'entry_key': key, 'observation': arm, 'f1_raw': deepcopy(base['f1_raw']),
                    'f2_outcomes': [deepcopy(table['rows'][sample][index]) for sample in table['samples']],
                    'f1_life_total': base.get('f1_life_total'), 'f2_life_total': base.get('f2_life_total'),
                    'sample_group_key': key})   # an arm's five samples are its own native runs
        return rows

    def tiers(self) -> dict:
        if not self.signals:
            return {}
        values = sorted(set(self.signals.values()) | {0.0})
        base_rank = values.index(0.0)
        # Private native namespace: only the card_reward menu reads these.
        # Ordinary card: labels also name merchant stock and must stay separate.
        return {'paired:'+label:[0,0,max(-8,min(8,values.index(value) - base_rank))]
                for label,value in sorted(self.signals.items())}

    def snapshot(self) -> dict:
        state = ('INACTIVE' if self.inactive_reason else 'ACTIVE' if self.seen
                 else 'WAITING_FOR_FINAL_F1' if self.metadata_seen else 'WAITING_FOR_NATIVE_METADATA')
        return {'enabled':self.enabled, 'state':state, 'inactive_reason':self.inactive_reason,
            'scope':SCOPE, 'samples_per_arm':SAMPLES, 'max_cards':CARDS,
            'entry_schedule':({'first':FIRST_THRESHOLD, 'every':self.every} if self.every else
                              {'first':FIRST_THRESHOLD, 'second':SECOND_THRESHOLD, 'factor':THRESHOLD_FACTOR}),
            'dedup':self.dedup, 'duplicate_tables_skipped':self.duplicate_tables, 'merge':self.merge,
            'signal_tables':{label:count for label,(_,count) in sorted(self.gain_sums.items())},
            'joint_rows':len(self.joint_rows_built), 'joint_tables_without_unique_base':len(self.joint_skipped),
            'distinct_entries':len(self.seen), 'next_entry_threshold':self.next_threshold,
            'tables_scheduled':len(self.tables), 'tables_with_signal':sum(t['status']=='PAIRED_SIGNAL' for t in self.tables),
            'scheduled_probes':sum(len(t['labels']) * len(t['samples']) for t in self.tables),
            'completed_batches':self.completed_batches, 'attempted_probes':self.attempted_probes,
            'usable_probes':self.usable_probes, 'errors_or_missing':self.errors,
            'unresolved_samples':sum(SAMPLES - t['complete_paired_samples'] for t in self.tables),
            'expanded_combat_nodes':self.nodes if not self.unknown_work_probes else None,
            'probe_search_seconds':self.search_seconds if not self.unknown_work_probes else None,
            'native_wall_seconds':self.native_seconds if not self.unknown_work_probes else None,
            'measured_expanded_combat_nodes':self.nodes, 'measured_probe_search_seconds':self.search_seconds,
            'measured_probe_native_seconds':self.native_seconds, 'unknown_work_probes':self.unknown_work_probes,
            'work_measurement_complete':not self.unknown_work_probes, 'tiers':self.tiers(),
            'tables':[{k:v for k,v in t.items() if k != 'rows'} for t in self.tables]}
