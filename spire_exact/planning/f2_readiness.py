"""Synthetic full-HP F2 readiness, never a real route or infeasibility proof.

Entry identities retain context, input bytes, initial bindings and full action
prefixes. The requested deck/relic group shares HEURISTIC signals across such
entries; it is not state equality and never restores or caches a game state.
Each real F1 entry keeps its own best native F1 outcome for the joint model.
No scheduler, real gate model, archive or native process is owned here.
"""
from collections import Counter, deque
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import math
import re

from ..canonical import canonical, ContractError
from .probes import gate_entries, probe_request, scaled
from .paired_card_probes import E45, MAP_OBSERVATION_KEYS, paired_outcome, valid_outcome
from .research_progress import require_research_progress

SAMPLES = 5
DEFAULT_EVERY = 20  # Taskbook coarse estimate; worker share is unmeasured.
SYNTHETIC_KINDS = frozenset(('gate_probe', 'paired_card_probe', 'real_card_menu_probe', 'f2_readiness_probe'))
SCOPE = 'synthetic allocation signals only; UNKNOWN is not loss; never state equality, pruning, cache, trajectory or proof'


def rng_samples(solver_seed, entry_ordinal, start=0):
    values = []
    for index in range(start + SAMPLES):
        value = int.from_bytes(hashlib.sha256(
            f'f2-readiness/v1:{solver_seed}:{entry_ordinal}:{index}'.encode()).digest()[:8], 'big')
        while value in values:
            value = (value + 1) % (1 << 64)
        values.append(value)
    return tuple(values[start:start + SAMPLES])


def _hash_label(value):
    return hashlib.sha256(value).hexdigest()


def _native_context(value):
    if not isinstance(value, dict) or any(key not in value for key in ('seed', 'character', 'ascension', 'unlocks')):
        raise ValueError('native_initial_context_missing')
    result = {key: value[key] for key in ('seed', 'character', 'ascension', 'unlocks')}
    if (not isinstance(result['seed'], str) or not result['seed']
            or result['character'] != 'IRONCLAD' or type(result['ascension']) is not int
            or result['ascension'] != 10 or result['unlocks'] != 'all'):
        raise ValueError('initial_context_outside_ironclad_a10_all_scope')
    return result


def _complete_inputs(value):
    if (not isinstance(value, dict) or not isinstance(value.get('sources'), dict) or not value['sources']
            or not isinstance(value.get('dependencies'), dict)
            or not isinstance(value.get('game_data_dir'), str) or not value['game_data_dir']):
        return False
    if any(not isinstance(key, str) or not key or not isinstance(digest, str)
           or re.fullmatch('[0-9a-f]{64}', digest) is None for key, digest in value['sources'].items()):
        return False
    return all(isinstance(value['dependencies'].get(key), str)
               and re.fullmatch('[0-9a-f]{64}', value['dependencies'][key])
               for key in ('sts2.dll', 'GodotSharp.dll', '0Harmony.dll'))


def _full_hp(value):
    if type(value) not in (int, str):
        raise ValueError('map_max_hp_unknown')
    try:
        number = Decimal(value)
        if not number.is_finite() or number != number.to_integral_value() or not 1 <= number <= 2147483647:
            raise ValueError('map_max_hp_not_supported_integer')
        return int(number)
    except InvalidOperation as error:
        raise ValueError('map_max_hp_unknown') from error


def _transport_free(request):
    return {key: value for key, value in request.items() if key not in ('out', 'command', 'compact')}


def _paired_signature(spec):
    value = deepcopy(spec)
    # Paired's in-memory gate is a tuple; its actual JSON wire is a list.
    value['gate'] = list(value.get('gate') or [])
    return canonical(value)


def _f1_entries(result):
    """Known gate outcomes plus a complete observed, interrupted F1 entry.

    Only this sampler widens the old gate_entries eligibility. A journal
    stopped before its first combat decision cannot manufacture an entry.
    """
    fights = gate_entries(result)
    known = {fight['index'] for fight in fights}
    trace, evidence = result.get('trace') or [], result.get('decision_evidence') or []
    seen = set()
    for index, row in enumerate(evidence[:len(trace)]):
        if not isinstance(row, dict) or not isinstance(row.get('observation'), dict):
            continue
        obs = row['observation']
        if (obs.get('act') != 2 or obs.get('room') != 'Boss'
                or row.get('phase') not in ('combat', 'select_cards')
                or type(obs.get('turn')) is not int or type(obs.get('floor')) is not int):
            continue
        floor = obs['floor']
        if floor in seen:
            continue
        seen.add(floor)
        if (len(seen) != 1 or index in known or index < 1 or not isinstance(trace[index - 1], dict)
                or trace[index - 1].get('kind') != 'map' or not MAP_OBSERVATION_KEYS <= set(obs)
                or not isinstance(obs.get('rng'), dict) or not obs['rng']):
            continue
        fights.append({'gate': (2, 0), 'index': index, 'enter': index - 1, 'entry': obs,
                       'outcome': None, 'lost': None, 'native_outcome_known': False})
    return sorted(fights, key=lambda fight: fight['index'])


def readiness_outcome(decision, request):
    """Strict paired reducer plus actual full replay-prefix equality."""
    try:
        if not isinstance(request, dict) or not isinstance(decision, dict):
            return None
        spec, info = request.get('probe'), decision.get('probe')
        if not isinstance(spec, dict) or not isinstance(info, dict):
            return None
        history = request.get('history')
        if (not isinstance(history, list) or spec.get('edits') != []
                or spec.get('enter') != {'kind': 'encounter', 'boss': 1}
                or type(spec.get('rng')) is not int or not 0 <= spec['rng'] < 1 << 64
                or type(spec.get('hp')) is not int or spec['hp'] < 1
                or not isinstance(spec.get('expected_entry_observation'), dict)
                or not MAP_OBSERVATION_KEYS <= set(spec['expected_entry_observation'])
                or type(info.get('won')) is not bool or type(decision.get('consumed')) is not int
                or type(info.get('entry_index')) is not int
                or not isinstance(decision.get('trace'), list)
                or len(decision['trace']) <= len(history)
                or not isinstance(decision['trace'][len(history)], dict)
                or decision['trace'][len(history)].get('kind') != 'probe_enter'
                or canonical(decision['trace'][:len(history)]) != canonical(history)):
            return None
        baseline = request.get('research_progress')
        if baseline is not None:
            report = decision.get('research_progress') or {}
            if (not isinstance(baseline, dict) or not isinstance(report, dict)
                    or report.get('schema') != 'spire-research-progress/v1'
                    or report.get('baseline_sha256') != baseline.get('baseline_sha256')
                    or report.get('checkpoint_restored') is not False):
                return None
        return paired_outcome(decision, request)
    except (ValueError, TypeError, KeyError, IndexError):
        return None


class F2Readiness:
    """Public hooks: observe, completed_evaluation, earmark_paired,
    next_stage, adopt_paired, absorb, model_entries, snapshot.

    model_entries/updates have in-memory bytes keys:
      rawentrykey = full entry guard; sample_group_key = heuristic group key;
      observation = this real F1 combat entry; f1_raw = best won/lost result;
      f2_outcomes = shared raw reductions including None; sources = every real
      source with entry_prefix (actions) and prefix_key (plain prefix bytes).
    JSON snapshots use SHA labels instead; those labels never decide equality.
    """
    def __init__(self, enabled=True, solver_seed=271828, *, every=DEFAULT_EVERY,
                 context=None, inputs=None, inactive_reason=None):
        if type(enabled) is not bool or type(solver_seed) is not int or type(every) is not int or every < 1:
            raise ValueError('invalid_f2_readiness_configuration')
        self.enabled, self.solver_seed, self.every = enabled, solver_seed, every
        self.context, self.inputs = deepcopy(context), deepcopy(inputs)
        self.inactive_reason = 'disabled' if not enabled else inactive_reason
        if enabled and not self.inactive_reason:
            try:
                _native_context(context)
                if not _complete_inputs(inputs):
                    raise ValueError('native_inputs_missing')
                canonical({'context': context, 'inputs': inputs})
            except (ValueError, TypeError, KeyError) as error:
                self.inactive_reason = str(error)
        self.entries, self.groups = [], []
        self.entry_keys, self.group_keys, self.source_entries, self.prefix_entries = {}, {}, {}, {}
        self.stage2 = deque()
        self.completed_labels, self.work_labels = set(), set()
        self.completed_native, self.credit = 0, False
        self.f1_life_total, self.f2_life_total = 1.0, 1.0
        self.counts, self.rejections = Counter(), Counter()
        self.nodes, self.search_seconds, self.native_seconds = 0, 0.0, 0.0
        self.unknown_work_probes = 0
        self.total_native_seconds, self.unknown_native_work = 0.0, 0
        self.metadata_seen = False

    def _initial(self, template):
        if canonical(_native_context(template)) != canonical(_native_context(self.context)):
            raise ValueError('source_initial_context_mismatch')
        advisor = template.get('advisor')
        if not isinstance(advisor, dict) or not isinstance(advisor.get('binary_identity'), dict) or not advisor['binary_identity']:
            raise ValueError('advisor_identity_missing')
        if any(not isinstance(key, str) or not key or not isinstance(digest, str)
               or re.fullmatch('[0-9a-f]{64}', digest) is None for key, digest in advisor['binary_identity'].items()):
            raise ValueError('advisor_identity_missing')
        progress = template.get('research_progress')
        if progress is not None:
            if not isinstance(progress, dict):
                raise ValueError('initial_progress_baseline_missing')
            progress = require_research_progress(progress, self.context, progress.get('native_identity'),
                                                 self.inputs['dependencies']['sts2.dll'])
        return {'native_context': _native_context(template),
                'advisor_binary_identity': deepcopy(advisor['binary_identity']),
                'research_progress': deepcopy(progress)}

    def entry_key_for_prefix(self, prefix, template=None, context=None):
        """prefix includes the entering F1 map move, exactly like a retry.
        Without a template only return one already-observed unambiguous key.
        """
        try:
            if (not isinstance(prefix, list) or not prefix or any(not isinstance(action, dict) for action in prefix)
                    or context is not None and canonical(context) != canonical(self.context)):
                return None
            if template is None:
                matches = self.prefix_entries.get(canonical(prefix), [])
                return self.entries[matches[0]]['key'] if len(matches) == 1 else None
            return canonical({'context': self.context, 'inputs': self.inputs,
                              'initial': self._initial(template), 'history': prefix})
        except (ValueError, TypeError, KeyError):
            return None

    def entry_for_source(self, label):
        ids = self.source_entries.get(label, [])
        return self._update(self.entries[ids[0]]) if len(ids) == 1 else None

    def _f1_raw(self, entry):
        if entry.get('lost') is not None:
            raw = {'won': False, 'outcome': None, 'lost': deepcopy(entry['lost'])}
        else:
            raw = {'won': True, 'outcome': entry.get('outcome'), 'lost': None}
        return raw if valid_outcome(raw) else None

    def _better_f1(self, raw, previous):
        if raw is None:
            return False
        if previous is None or raw['won'] and not previous['won']:
            return True
        if raw['won'] != previous['won']:
            return False
        return raw['outcome'] > previous['outcome'] if raw['won'] else raw['lost'][0] > previous['lost'][0]

    def observe(self, result, label, searched, template, family=None):
        if not self.enabled or self.inactive_reason:
            return []
        if not isinstance(result, dict):
            self.rejections['source_result_missing'] += 1
            return []
        if result.get('synthetic'):
            return []
        campaign = result.get('campaign') or {}
        if type(campaign.get('act_count')) is not int or type(campaign.get('final_act_boss_count')) is not int:
            self.rejections['native_campaign_metadata_missing'] += 1
            return []
        self.metadata_seen = True
        if campaign['act_count'] != 3 or campaign['final_act_boss_count'] < 2:
            self.rejections['campaign_outside_three_act_double_boss_scope'] += 1
            return []
        updates = []
        try:
            initial = self._initial(template)
            for fight in _f1_entries(result):
                if tuple(fight['gate']) != (2, 0):
                    continue
                raw = self._f1_raw(fight)
                if raw is not None and not raw['won']:
                    self.f1_life_total = max(self.f1_life_total, raw['lost'][1])
                if fight['index'] < searched:
                    continue
                index, enter = fight['index'], fight['enter']
                trace, evidence = result.get('trace') or [], result.get('decision_evidence') or []
                row = evidence[enter]
                if not isinstance(row, dict):
                    self.rejections['complete_native_f1_map_guard_missing'] += 1
                    continue
                before = row.get('observation') or {}
                if (row.get('phase') != 'map' or not isinstance(before, dict) or not MAP_OBSERVATION_KEYS <= set(before)
                        or not isinstance(before.get('rng'), dict) or not before['rng']
                        or not isinstance(before.get('deck'), list) or not isinstance(before.get('relics'), list)
                        or before.get('turn') is not None or before.get('act') != 2
                        or not any(canonical(trace[enter]) == canonical(action) for action in row.get('available_actions', []))):
                    self.rejections['complete_native_f1_map_guard_missing'] += 1
                    continue
                hp = _full_hp(before.get('max_hp'))
                deck = []
                for card in before['deck']:
                    if (not isinstance(card, dict) or not isinstance(card.get('id'), str) or not card['id']
                            or type(card.get('upgrade')) is not int or card['upgrade'] < 0):
                        raise ValueError('deck_identity_missing')
                    deck.append([card['id'], card['upgrade']])
                if any(not isinstance(relic, str) or not relic for relic in before['relics']):
                    raise ValueError('relic_identity_missing')
                prefix = deepcopy(trace[:index])
                key = self.entry_key_for_prefix(prefix, template)
                if key is None:
                    raise ValueError('entry_guard_missing')
                if key in self.entry_keys:
                    entry = self.entries[self.entry_keys[key]]
                    if canonical(entry['map_observation']) != canonical(before):
                        self.rejections['same_prefix_map_guard_changed'] += 1
                        continue
                    if self._better_f1(raw, entry['f1_raw']):
                        entry['f1_raw'], entry['observation'] = deepcopy(raw), deepcopy(fight['entry'])
                else:
                    group_key = canonical({'context': self.context, 'inputs': self.inputs, 'initial': initial,
                                           'deck_multiset': sorted(deck), 'relics': before['relics']})
                    if group_key not in self.group_keys:
                        group_id = len(self.groups)
                        self.group_keys[group_key] = group_id
                        self.groups.append({'key': group_key, 'owner': len(self.entries), 'sample_owner': len(self.entries),
                            'entries': [], 'state': 'UNKNOWN', 'rows': [], 'stages': {}, 'signatures': {}, 'paired': None})
                    group_id = self.group_keys[group_key]
                    entry = {'id': len(self.entries), 'key': key, 'group': group_id, 'sources': [],
                        'prefix': prefix, 'map_history': deepcopy(trace[:enter]), 'map_observation': deepcopy(before),
                        'observation': deepcopy(fight['entry']), 'f1_raw': deepcopy(raw), 'full_hp': hp,
                        'template': deepcopy(template), 'trace': deepcopy(prefix), 'enter': enter}
                    self.entry_keys[key] = entry['id']
                    self.prefix_entries.setdefault(canonical(prefix), []).append(entry['id'])
                    self.entries.append(entry)
                    self.groups[group_id]['entries'].append(entry['id'])
                source = {'label': label, 'family': family, 'entry_id': entry['id'], 'index': index,
                          'entry_prefix': deepcopy(prefix), 'prefix_key': canonical(prefix)}
                if not any(item['label'] == label for item in entry['sources']):
                    entry['sources'].append(source)
                ids = self.source_entries.setdefault(label, [])
                if entry['id'] not in ids:
                    ids.append(entry['id'])
                updates.append(self._update(entry))
        except (ValueError, TypeError, KeyError, IndexError) as error:
            self.rejections[str(error)] += 1
        return updates

    def record_worker_work(self, label, workload=None):
        if label in self.work_labels:
            return
        self.work_labels.add(label)
        seconds = None
        if isinstance(workload, dict):
            seconds = workload.get('native_seconds', workload.get('measured_probe_native_seconds'))
            if seconds is None and type(workload.get('native_wall_us')) is int and workload['native_wall_us'] >= 0:
                seconds = workload['native_wall_us'] / 1e6
            if seconds is None and isinstance(workload.get('performance'), dict):
                microseconds = workload['performance'].get('wall_us')
                seconds = microseconds / 1e6 if type(microseconds) is int and microseconds >= 0 else None
        if type(seconds) in (int, float) and math.isfinite(seconds) and seconds >= 0:
            self.total_native_seconds += seconds
            if workload.get('unknown_work_probes', 0) or workload.get('complete') is False:
                self.unknown_native_work += 1
        else:
            self.unknown_native_work += 1

    def completed_evaluation(self, label, kind=None, *, synthetic=False, cache_hit=False, workload=None):
        if not self.enabled or self.inactive_reason or label in self.completed_labels:
            return
        self.completed_labels.add(label)
        if not cache_hit:
            self.record_worker_work(label, workload)
        if synthetic or kind in SYNTHETIC_KINDS or cache_hit:
            return
        self.completed_native += 1
        if self.completed_native % self.every == 0:
            self.credit = True  # One token maximum; never build a probe burst.

    def _request(self, entry, sample):
        request = probe_request(entry['template'], entry['trace'], {'enter': entry['enter']}, [],
                                hp=entry['full_hp'], rng=sample, advisor_patch=E45)
        request['probe'].update(enter={'kind': 'encounter', 'boss': 1},
                                expected_entry_observation=deepcopy(entry['map_observation']))
        # The legacy factory deliberately whitelists old probe fields. Only
        # this new probe copies the genuine job baseline, never expected state.
        if entry['template'].get('research_progress') is not None:
            request['research_progress'] = deepcopy(entry['template']['research_progress'])
        return request

    def _paired_match(self, spec):
        if (spec.get('kind') != 'paired_card_probe' or tuple(spec.get('gate') or []) != (2, 0)
                or type(spec.get('table')) is not int or spec['table'] < 0 or not spec.get('labels')
                or spec['labels'][0] != 'card_skip' or not spec.get('requests')
                or type(spec.get('sample')) is not int or not 0 <= spec['sample'] < 1 << 64):
            return None
        request = _transport_free(spec['requests'][0])
        for entry_id in self.source_entries.get(spec.get('base'), []):
            entry = self.entries[entry_id]
            if canonical(request.get('research_progress')) != canonical(entry['template'].get('research_progress')):
                self.rejections['paired_initial_baseline_mismatch'] += 1
                continue
            if canonical(request) == canonical(self._request(entry, spec['sample'])):
                return entry
        return None

    def earmark_paired(self, specs):
        """Reserve a whole existing five-sample base arm before dispatch."""
        buckets = {}
        for spec in specs:
            try:
                entry = self._paired_match(spec)
                if entry is not None:
                    buckets.setdefault(entry['id'], []).append(spec)
            except (ValueError, TypeError, KeyError, IndexError):
                self.rejections['invalid_paired_earmark'] += 1
        reserved = 0
        for entry_id, rows in buckets.items():
            entry = self.entries[entry_id]
            group = self.groups[entry['group']]
            samples = [row['sample'] for row in rows]
            if len(rows) != SAMPLES or len(set(samples)) != SAMPLES or group['stages'] or group['paired']:
                continue
            group['sample_owner'] = entry_id
            group['paired'] = {'samples': samples, 'signatures': {row['sample']: _paired_signature(row) for row in rows}, 'rows': {}}
            reserved += 1
        self.counts['paired_groups_reserved'] += reserved
        return reserved

    def _priority_group(self, keys, candidates):
        requested = {self.entries[self.entry_keys[key]]['group'] for key in keys
                     if isinstance(key, bytes) and key in self.entry_keys}
        return next((group for group in candidates if self.group_keys[group['key']] in requested), None)

    def next_stage(self, retry_entries=(), focus_entries=()):
        if not self.enabled or self.inactive_reason or not self.credit:
            return None
        group = None
        while self.stage2:
            candidate = self.groups[self.stage2[0]]
            if candidate['state'] == 'PENDING' and 2 not in candidate['stages']:
                group = candidate
                break
            self.stage2.popleft()
        stage = 2 if group is not None else 1
        if group is None:
            candidates = [candidate for candidate in self.groups if not candidate['stages'] and not candidate['paired']]
            group = (self._priority_group(retry_entries, candidates) or self._priority_group(focus_entries, candidates)
                     or (candidates[0] if candidates else None))
        if group is None:
            return None
        entry = self.entries[group['sample_owner']]
        samples = list(rng_samples(self.solver_seed, entry['id'], (stage - 1) * SAMPLES))
        if group['paired']:
            # Deterministically avoid the five reused paired RNGs as well.
            occupied = set(group['paired']['samples'])
            for index, sample in enumerate(samples):
                while sample in occupied:
                    sample = (sample + 1) % (1 << 64)
                samples[index] = sample
                occupied.add(sample)
        spec = {'kind': 'f2_readiness_probe', 'category': 'probe', 'synthetic': True,
                'gate': (2, 1), 'table': self.group_keys[group['key']], 'entry': entry['id'],
                'stage': stage, 'base': entry['sources'][0]['label'], 'samples': samples,
                'labels': ['card_skip'] * SAMPLES, 'requests': [self._request(entry, sample) for sample in samples],
                'request': {'history': deepcopy(entry['map_history']), 'generate_candidate': False}}
        # Canonical spec uses a JSON list rather than a Python-only tuple.
        spec['gate'] = [2, 1]
        group['stages'][stage] = 'INFLIGHT'
        group['signatures'][stage] = canonical(spec)
        self.credit = False
        self.counts['dispatched_stages'] += 1
        self.counts['scheduled_probes'] += SAMPLES
        return spec

    def _set_stage(self, group, stage, rows):
        group['stages'][stage] = 'COMPLETE'
        group['rows'].extend(rows)
        outcomes = [row['outcome'] for row in group['rows']]
        for outcome in outcomes:
            if valid_outcome(outcome) and not outcome['won']:
                self.f2_life_total = max(self.f2_life_total, outcome['lost'][1])
        if any(valid_outcome(outcome) and outcome['won'] for outcome in outcomes):
            group['state'] = 'VIABLE'
        elif stage == 1 and len(outcomes) == SAMPLES and all(valid_outcome(outcome) for outcome in outcomes):
            group['state'] = 'PENDING'
            self.stage2.append(self.group_keys[group['key']])
        elif len(outcomes) == 2 * SAMPLES and all(valid_outcome(outcome) for outcome in outcomes):
            group['state'] = 'DEAD_AT_FULL_HP'
        else:
            group['state'] = 'UNKNOWN'

    def _work(self, outcomes, workload):
        self.counts['attempted_probes'] += SAMPLES
        self.counts['usable_probes'] += sum(valid_outcome(row) for row in outcomes)
        self.counts['errors_or_missing'] += sum(not valid_outcome(row) for row in outcomes)
        if not isinstance(workload, dict):
            self.unknown_work_probes += SAMPLES
            return
        fields_known = True
        for key, attr in (('measured_expanded_combat_nodes', 'nodes'),
                          ('measured_probe_search_seconds', 'search_seconds'),
                          ('measured_probe_native_seconds', 'native_seconds')):
            value = workload.get(key)
            if (type(value) in (int, float) and math.isfinite(value) and value >= 0
                    and (attr != 'nodes' or type(value) is int)):
                setattr(self, attr, getattr(self, attr) + value)
            else:
                fields_known = False
        unknown = workload.get('unknown_work_probes')
        self.unknown_work_probes += (unknown if fields_known and type(unknown) is int and unknown >= 0
                                    and workload.get('work_measurement_complete') is not False else SAMPLES)

    def absorb(self, spec, outcomes, workload=None, *, label=None):
        group = self.groups[spec['table']]
        stage = spec['stage']
        if group['stages'].get(stage) != 'INFLIGHT' or canonical(spec) != group['signatures'].get(stage):
            raise ValueError('unexpected_or_duplicate_readiness_stage')
        if not isinstance(outcomes, list) or len(outcomes) > SAMPLES:
            outcomes = [None] * SAMPLES
            self.rejections['readiness_outcome_count'] += 1
        else:
            outcomes = list(outcomes) + [None] * (SAMPLES - len(outcomes))
        outcomes = [deepcopy(row) if valid_outcome(row) else None for row in outcomes]
        self._work(outcomes, workload)
        if label is not None:
            self.record_worker_work(label, workload)
        rows = [{'sample': sample, 'origin': 'f2-readiness/v1', 'outcome': outcome}
                for sample, outcome in zip(spec['samples'], outcomes)]
        self._set_stage(group, stage, rows)
        return self._all_updates()

    def adopt_paired(self, spec, outcomes, workload=None):
        """Only the strict card_skip result of a previously earmarked sample.
        The full paired batch's cost is not misattributed to its base arm.
        """
        entry = self._paired_match(spec)
        if entry is None:
            self.rejections['paired_entry_guard_mismatch'] += 1
            return []
        group = self.groups[entry['group']]
        paired = group['paired']
        sample = spec['sample']
        if (not paired or paired['signatures'].get(sample) != _paired_signature(spec)
                or sample in paired['rows'] or group['sample_owner'] != entry['id']):
            self.rejections['unexpected_or_duplicate_paired_base'] += 1
            return []
        outcome = outcomes[0] if isinstance(outcomes, list) and outcomes else None
        outcome = deepcopy(outcome) if valid_outcome(outcome) else None
        paired['rows'][sample] = outcome
        self.counts['reused_paired_probes'] += 1
        self.counts['reused_paired_usable'] += int(outcome is not None)
        self.counts['errors_or_missing'] += int(outcome is None)
        if outcome is not None and outcome['won']:
            group['state'] = 'VIABLE'
        if len(paired['rows']) == SAMPLES:
            rows = [{'sample': value, 'origin': 'paired-card/v1:card_skip', 'outcome': paired['rows'][value]}
                    for value in paired['samples']]
            self._set_stage(group, 1, rows)
        return self._all_updates()

    def set_f1_life_total(self, total):
        if type(total) in (int, float) and math.isfinite(total) and total > 0:
            self.f1_life_total = max(self.f1_life_total, total)

    def set_f2_life_total(self, total):
        """Include the real F2 gate's observed life total without model mixing."""
        if type(total) in (int, float) and math.isfinite(total) and total > 0:
            self.f2_life_total = max(self.f2_life_total, total)

    def _update(self, entry):
        group = self.groups[entry['group']]
        outcomes = [row['outcome'] for row in group['rows']]
        complete = len(outcomes) in (SAMPLES, 2 * SAMPLES) and all(valid_outcome(row) for row in outcomes)
        mean = sum(scaled(row, self.f2_life_total) for row in outcomes) / len(outcomes) if complete else None
        raw = entry['f1_raw']
        return {'rawentrykey': entry['key'], 'entry_key': entry['key'], 'sample_group_key': group['key'],
            'entry_id': entry['id'], 'owner': group['sample_owner'],
            'shared_from': group['sample_owner'] if entry['id'] != group['sample_owner'] else None,
            'status': group['state'], 'mean': mean, 'f2_mean': mean,
            'observation': deepcopy(entry['observation']), 'map_observation': deepcopy(entry['map_observation']),
            'f1_raw': deepcopy(raw), 'f1_outcome': scaled(raw, self.f1_life_total) if raw is not None else None,
            'f1_life_total': self.f1_life_total, 'f2_life_total': self.f2_life_total,
            'f2_outcomes': deepcopy(outcomes), 'sources': deepcopy(entry['sources']),
            'group_sources': [deepcopy(source) for index in group['entries'] for source in self.entries[index]['sources']],
            'initial_context_scope': ('explicit_native_progress_baseline' if entry['template'].get('research_progress') is not None
                                      else 'legacy_no_explicit_progress_guard'),
            'scope': SCOPE, 'sharing_scope': 'deck/relic heuristic group; distinct native entry guards remain independent'}

    def _all_updates(self):
        return [self._update(entry) for entry in self.entries]

    def model_entries(self):
        return self._all_updates()

    def entry_updates(self):
        return self._all_updates()

    def state_for(self, entry_key):
        index = self.entry_keys.get(entry_key)
        return self._update(self.entries[index]) if index is not None else None

    def snapshot(self, total_native_seconds=None, total_native_work_complete=None):
        denominator = self.total_native_seconds if total_native_seconds is None else total_native_seconds
        denominator_known = self.unknown_native_work == 0 if total_native_work_complete is None else total_native_work_complete
        share = (self.native_seconds / denominator if denominator_known and not self.unknown_work_probes
                 and type(denominator) in (int, float) and math.isfinite(denominator) and denominator > 0 else None)
        tables = []
        for index, group in enumerate(self.groups):
            entry = self.entries[group['sample_owner']]
            update = self._update(entry)
            tables.append({'group': index, 'group_sha256_label': _hash_label(group['key']),
                'owner': group['sample_owner'], 'shared_entries': list(group['entries']),
                'status': group['state'], 'mean': update['mean'], 'samples': deepcopy(group['rows']),
                'paired_received': len(group['paired']['rows']) if group['paired'] else 0,
                'stage_status': dict(group['stages'])})
        return {'enabled': self.enabled, 'inactive_reason': self.inactive_reason,
            'state': 'INACTIVE' if self.inactive_reason else 'ACTIVE' if self.entries else 'WAITING_FOR_NATIVE_METADATA',
            'scope': SCOPE, 'every': self.every, 'completed_nonsynthetic_native': self.completed_native,
            'pending_stage_credit': int(self.credit), 'distinct_real_entries': len(self.entries),
            'independent_f2_sample_groups': len(self.groups),
            'states': dict(Counter(self.groups[entry['group']]['state'] for entry in self.entries)),
            'counts': dict(self.counts), 'source_rejections': dict(self.rejections),
            'f1_life_total': self.f1_life_total, 'f2_life_total': self.f2_life_total,
            'measured_expanded_combat_nodes': self.nodes, 'measured_probe_search_seconds': self.search_seconds,
            'measured_probe_native_seconds': self.native_seconds, 'unknown_work_probes': self.unknown_work_probes,
            'work_measurement_complete': not self.unknown_work_probes,
            'worker_share': share, 'measured_total_native_seconds': denominator,
            'unknown_native_work_reports': self.unknown_native_work,
            'worker_share_scope': 'additional readiness probes only; paired reused arms are counted by their original batch, not twice',
            'target_worker_share': 'approximately 1/8; not measured or auto-tuned by this module', 'groups': tables}
