"""Pure F2 readiness protocol fixtures; no native process or solver."""
from copy import deepcopy
import hashlib
import json
import unittest

from spire_exact.canonical import canonical
from spire_exact.mode1 import context
from spire_exact.planning.archive import failure_combat_prefix
from spire_exact.planning.f2_readiness import F2Readiness, rng_samples, readiness_outcome
from spire_exact.planning.paired_card_probes import E45, rng_samples as paired_rng_samples
from spire_exact.planning.probes import probe_request

CTX = context('42', 'IRONCLAD', 10, 'all')
SHA = '0' * 64
INPUTS = {'sources': {'Fixture.cs': SHA},
          'dependencies': {'sts2.dll': SHA, 'GodotSharp.dll': SHA, '0Harmony.dll': SHA},
          'game_data_dir': 'fixture-only', 'native_runtime': {'profile': 'fixture'}}


def baseline(tag=0):
    raw = json.dumps({'schema_version': 0, 'fixture_initial': tag})
    return {'schema': 'spire-research-progress/v1', 'game_sha256': SHA,
            'native_identity': {'host_sha256': SHA, 'game_sha256': SHA},
            'context': {key: CTX[key] for key in ('seed', 'character', 'ascension', 'unlocks')} | {
                'information': 'full', 'objective': 'whole_run_victory/v1'},
            'baseline_snapshot': raw, 'baseline_sha256': hashlib.sha256(raw.encode()).hexdigest()}


def template(explicit_baseline=True):
    result = {key: CTX[key] for key in ('seed', 'character', 'ascension', 'unlocks')}
    result.update(history=[], generate_candidate=True, policy_seed=7,
                  capture_checkpoints=True, checkpoint='must-not-escape', low_io=True,
                  advisor={'binary_identity': {'Fixture.dll': SHA}, 'nodes': 60000,
                           'gate_plans': {'FinalBoss': {'members': [{'beam': 270, 'nodes': 540000}]}}})
    if explicit_baseline:
        result['research_progress'] = baseline()
    return result


def trajectory(tag=0, card='STRIKE', removed=60, total=100, opening=0):
    # `opening`: card selections asked at F1 turn 1 before its first combat
    # decision (a combat-start effect such as a relic exhausting a card).
    quiet = {'act': 2, 'floor': 48, 'room': None, 'hp': '60', 'max_hp': '80', 'gold': 100,
             'deck': [{'id': card, 'upgrade': 0}], 'relics': ['BURNING_BLOOD'], 'potions': [None],
             'strategic': {}, 'selection': None, 'hand': None, 'turn': None, 'energy': None,
             'block': '0', 'enemies': None, 'character': 'IRONCLAD', 'score': 0,
             'rng': {'fixture_stream': tag}}
    event = {'kind': 'event', 'index': 0, 'key': 'FIXTURE'}
    move, combat = {'kind': 'map', 'col': tag, 'row': 15}, {'kind': 'end_turn'}
    fight = deepcopy(quiet)
    fight.update(floor=49, room='Boss', turn=1, max_hp='90',
                 enemies=[{'id': 'F1', 'combat_id': 1, 'hp': str(total), 'max_hp': str(total)}])
    picks = [{'kind': 'select_cards', 'indices': [index]} for index in range(opening)]
    return {'campaign': {'act_count': 3, 'final_act_boss_count': 2},
            'status': 'TERMINAL', 'value': [0], 'reason': None, 'native_terminal_observed': False,
            'trace': [event, move, *picks, combat], 'decision_evidence': [
                {'phase': 'event', 'observation': deepcopy(quiet), 'available_actions': [event]},
                {'phase': 'map', 'observation': deepcopy(quiet), 'available_actions': [move]},
                *({'phase': 'select_cards', 'available_actions': [pick],
                   'observation': dict(deepcopy(fight), selection={'purpose': 'Exhaust'})} for pick in picks),
                {'phase': 'combat', 'observation': fight, 'available_actions': [combat]}],
            'observation': dict(quiet, floor=49, room='Boss', hp='0'),
            'terminal_combat': {'act': 2, 'floor': 49, 'turn': 2,
                               'enemies': [{'id': 'F1', 'combat_id': 1, 'hp': str(total - removed), 'max_hp': str(total)}]}}


def loss(removed=40, total=100):
    return {'won': False, 'outcome': None, 'lost': (float(removed), float(total)), 'nodes': 10, 'search_seconds': 0.1}


def win():
    return {'won': True, 'outcome': 1.5, 'lost': None, 'hp': 40.0, 'max_hp': 80.0}


def ready(every=1, **options):
    return F2Readiness(True, 123, every=every, context=CTX, inputs=INPUTS, **options)


def credit(probes, count=1, offset=0):
    for index in range(count):
        probes.completed_evaluation('native-%d' % (index + offset), 'macro_focus', workload={'native_seconds': 10})


def paired_specs(result, source_template, copy_baseline=False, base='source'):
    specs = []
    for sample in paired_rng_samples(123, 0):
        request = probe_request(source_template, result['trace'], {'enter': 1}, [], hp=80, rng=sample, advisor_patch=E45)
        request['probe'].update(enter={'kind': 'encounter', 'boss': 1},
                                expected_entry_observation=deepcopy(result['decision_evidence'][1]['observation']))
        if copy_baseline and source_template.get('research_progress') is not None:
            request['research_progress'] = deepcopy(source_template['research_progress'])
        added = deepcopy(request); added['probe']['edits'] = [{'op': 'add', 'card': 'OTHER', 'upgrade': 0}]
        specs.append({'kind': 'paired_card_probe', 'category': 'probe', 'gate': (2, 0), 'table': 0,
                      'sample': sample, 'base': base, 'labels': ['card_skip', 'card:OTHER'],
                      'requests': [request, added], 'request': {'history': result['trace'][:1], 'generate_candidate': False}})
    return specs


class F2ReadinessTests(unittest.TestCase):
    def test_off_is_inert_and_full_entry_identity_maps_retry_and_source(self):
        source, request = trajectory(), template()
        before = canonical(request)
        off = F2Readiness(False, 123, context=CTX, inputs=INPUTS)
        self.assertEqual(off.observe(source, 'source', 0, request), [])
        credit(off, 30); self.assertIsNone(off.next_stage())
        self.assertEqual(canonical(request), before)
        probes = ready(); updates = probes.observe(source, 'source', 0, request)
        key = probes.entry_key_for_prefix(source['trace'][:2], request)
        self.assertEqual(updates[0]['entry_key'], key)
        self.assertEqual(probes.entry_key_for_prefix(source['trace'][:2]), key)
        self.assertEqual(probes.entry_for_source('source')['entry_key'], key)
        self.assertIsNone(probes.entry_key_for_prefix(source['trace'][:1]))
        bad = deepcopy(request); bad['seed'] = 'other'
        self.assertIsNone(probes.entry_key_for_prefix(source['trace'][:2], bad))

    def test_retry_prefix_after_an_opening_selection_names_the_same_entry(self):
        # The gate retry prefix ends at F1's first combat decision
        # (failure_combat_prefix); the readiness entry starts at the first
        # in-combat decision, here a selection. Both name the same entry.
        source, request = trajectory(opening=1), template()
        probes = ready(); updates = probes.observe(source, 'source', 0, request)
        key = probes.entry_key_for_prefix(source['trace'][:2], request)
        self.assertEqual(updates[0]['entry_key'], key)
        retry = failure_combat_prefix(source)['prefix']
        self.assertEqual(retry, source['trace'][:3])
        self.assertEqual(probes.entry_key_for_prefix(retry), key)
        self.assertEqual(probes.entry_key_for_prefix(source['trace'][:2]), key)
        self.assertIsNone(probes.entry_key_for_prefix(source['trace'][:4]))
        again = ready(); again.observe(source, 'source', 0, request); again.observe(source, 'other', 0, request)
        self.assertEqual(again.entry_key_for_prefix(retry), key)

    def test_stage_requests_full_hp_e45_namespace_and_explicit_baseline_are_guarded(self):
        source, request = trajectory(), template()
        probes = ready(); probes.observe(source, 'source', 0, request); credit(probes)
        spec = probes.next_stage()
        self.assertEqual(spec['samples'], list(rng_samples(123, 0)))
        self.assertEqual(len(spec['requests']), 5)
        self.assertTrue(spec['synthetic'])
        self.assertEqual(spec['gate'], [2, 1])
        for sent in spec['requests']:
            self.assertEqual(sent['history'], source['trace'][:1])
            self.assertEqual(sent['probe']['hp'], 80)  # Not the F1 start-hook maximum 90.
            self.assertEqual(sent['probe']['enter'], {'kind': 'encounter', 'boss': 1})
            self.assertEqual(sent['probe']['edits'], [])
            self.assertEqual(sent['probe']['expected_entry_observation'], source['decision_evidence'][1]['observation'])
            self.assertEqual(sent['advisor']['gate_plans'], E45['gate_plans'])
            self.assertEqual(sent['research_progress'], request['research_progress'])
            self.assertNotIn('checkpoint', sent); self.assertFalse(sent['capture_checkpoints'])
            canonical(sent)
        spec['requests'][0]['research_progress']['baseline_snapshot'] = 'mutated'
        self.assertNotEqual(spec['requests'][0]['research_progress'], request['research_progress'])
        self.assertEqual(spec['requests'][1]['research_progress'], request['research_progress'])

    def test_two_valid_loss_stages_only_then_dead_and_rng_sets_are_distinct(self):
        probes = ready(); probes.observe(trajectory(), 'source', 0, template()); credit(probes)
        first = probes.next_stage(); probes.absorb(first, [loss()] * 5)
        self.assertEqual(probes.entry_for_source('source')['status'], 'PENDING')
        self.assertIsNone(probes.next_stage())
        credit(probes, offset=1); second = probes.next_stage()
        self.assertEqual(second['stage'], 2)
        self.assertTrue(set(first['samples']).isdisjoint(second['samples']))
        probes.absorb(second, [loss()] * 5)
        self.assertEqual(probes.entry_for_source('source')['status'], 'DEAD_AT_FULL_HP')
        self.assertEqual(len(probes.model_entries()[0]['f2_outcomes']), 10)
        with self.assertRaises(ValueError): probes.absorb(second, [loss()] * 5)

    def test_any_valid_win_is_viable_but_missing_or_invalid_loss_never_creates_dead(self):
        for outcomes, state in (([win(), None, loss(), loss(), loss()], 'VIABLE'),
                                ([None] + [loss()] * 4, 'UNKNOWN'), ([loss(0, 0)] * 5, 'UNKNOWN')):
            with self.subTest(state=state):
                probes = ready(); probes.observe(trajectory(), 'source', 0, template()); credit(probes)
                probes.absorb(probes.next_stage(), outcomes)
                row = probes.entry_for_source('source')
                self.assertEqual(row['status'], state)
                self.assertIsNone(row['mean'])
                credit(probes, offset=1); self.assertIsNone(probes.next_stage())
        probes = ready(); probes.observe(trajectory(), 'source', 0, template()); credit(probes)
        probes.absorb(probes.next_stage(), [loss()] * 5)
        credit(probes, offset=1); probes.absorb(probes.next_stage(), [loss()] * 4 + [None])
        self.assertEqual(probes.entry_for_source('source')['status'], 'UNKNOWN')

    def test_credit_counts_native_completions_not_cache_or_synthetic_and_cannot_burst(self):
        probes = ready(every=2); probes.observe(trajectory(), 'source', 0, template())
        probes.completed_evaluation('cache', 'macro_focus', cache_hit=True)
        probes.completed_evaluation('synthetic', 'paired_card_probe')
        probes.completed_evaluation('synthetic2', 'other', synthetic=True)
        self.assertEqual(probes.completed_native, 0)
        credit(probes, 1); self.assertIsNone(probes.next_stage())
        credit(probes, 9, offset=1)
        self.assertEqual(probes.snapshot()['pending_stage_credit'], 1)
        self.assertIsNotNone(probes.next_stage()); self.assertIsNone(probes.next_stage())
        probes.completed_evaluation('native-0', 'macro_focus')
        self.assertEqual(probes.completed_native, 10)

    def test_priority_stage_two_then_retry_then_focus_then_fifo(self):
        probes = ready()
        for tag, card in ((0, 'A'), (1, 'B'), (2, 'C')):
            probes.observe(trajectory(tag, card), 'source%d' % tag, 0, template())
        keys = [probes.entry_for_source('source%d' % tag)['entry_key'] for tag in range(3)]
        credit(probes); spec = probes.next_stage(retry_entries=[keys[1]], focus_entries=[keys[2]])
        self.assertEqual(spec['entry'], 1)
        probes.absorb(spec, [loss()] * 5); credit(probes, offset=1)
        second = probes.next_stage(retry_entries=[keys[0]])
        self.assertEqual(second['stage'], 2); self.assertEqual(second['entry'], 1)
        probes.absorb(second, [loss()] * 5); credit(probes, offset=2)
        focus = probes.next_stage(focus_entries=[keys[2]])
        self.assertEqual(focus['entry'], 2)
        probes.absorb(focus, [win()] * 5); credit(probes, offset=3)
        self.assertEqual(probes.next_stage()['entry'], 0)

    def test_same_deck_relic_group_shares_heuristic_results_not_real_entry_identity(self):
        probes = ready(); request = template()
        first = probes.observe(trajectory(0, removed=20), 'one', 0, request)[0]
        second = probes.observe(trajectory(1, removed=80), 'two', 0, request)[0]
        self.assertNotEqual(first['entry_key'], second['entry_key'])
        self.assertEqual(first['sample_group_key'], second['sample_group_key'])
        credit(probes); probes.absorb(probes.next_stage(), [win()] * 5)
        rows = probes.model_entries()
        self.assertEqual(len(rows), 2)  # Each genuine F1 entry has its own target.
        self.assertNotEqual(rows[0]['f1_raw'], rows[1]['f1_raw'])
        self.assertEqual(rows[0]['f2_outcomes'], rows[1]['f2_outcomes'])
        self.assertEqual(rows[1]['shared_from'], 0)
        self.assertEqual(probes.snapshot()['independent_f2_sample_groups'], 1)
        self.assertEqual(probes.entry_for_source('two')['status'], 'VIABLE')
        credit(probes, offset=1); self.assertIsNone(probes.next_stage())

    def test_duplicate_real_entry_keeps_best_native_f1_and_all_source_notifications(self):
        probes = ready(); request = template()
        probes.observe(trajectory(removed=20), 'one', 0, request)
        probes.observe(trajectory(removed=80), 'two', 0, request)
        probes.observe(trajectory(removed=10), 'three', 0, request)
        row = probes.model_entries()[0]
        self.assertEqual(row['f1_raw']['lost'][0], 80)
        self.assertEqual([source['label'] for source in row['sources']], ['one', 'two', 'three'])
        self.assertEqual(len(probes.entries), 1)
        probes.set_f1_life_total(200)
        self.assertEqual(probes.model_entries()[0]['f1_outcome'], 0.4)

    def test_context_inputs_missing_map_rng_deck_or_baseline_mismatch_are_unknown(self):
        self.assertTrue(F2Readiness(True, context=None, inputs=INPUTS).inactive_reason)
        self.assertTrue(F2Readiness(True, context=CTX, inputs={}).inactive_reason)
        for missing in ('rng', 'deck', 'gold', 'max_hp'):
            probes = ready(); source = trajectory(); source['decision_evidence'][1]['observation'].pop(missing)
            self.assertEqual(probes.observe(source, 'bad', 0, template()), [])
            self.assertEqual(len(probes.entries), 0)
        probes = ready(); source = trajectory(); source.pop('campaign')
        self.assertEqual(probes.observe(source, 'legacy', 0, template()), [])
        bad = template(); bad['research_progress']['baseline_sha256'] = '1' * 64
        self.assertEqual(probes.observe(trajectory(), 'bad-baseline', 0, bad), [])
        self.assertTrue(probes.rejections)

    def test_full_guard_separates_initial_baselines_and_rejects_changed_map_at_same_prefix(self):
        probes = ready(); probes.observe(trajectory(), 'one', 0, template())
        changed = trajectory(); changed['decision_evidence'][1]['observation']['rng']['fixture_stream'] = 99
        self.assertEqual(probes.observe(changed, 'bad-same-prefix', 0, template()), [])
        alternate = template(); alternate['research_progress'] = baseline(1)
        probes.observe(trajectory(), 'other-baseline', 0, alternate)
        self.assertEqual(len(probes.groups), 2)
        self.assertIsNone(probes.entry_key_for_prefix(trajectory()['trace'][:2]))

    def test_observed_interrupted_f1_can_be_sampled_but_precombat_journal_cannot(self):
        source = trajectory()
        source.update(status='UNSUPPORTED', reason='NATIVE_TASK_MEMORY_BUDGET', value=None)
        source['observation']['hp'] = '40'
        source.pop('terminal_combat')
        probes = ready()
        rows = probes.observe(source, 'interrupted', 0, template())
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]['f1_raw'])
        self.assertIsNone(rows[0]['f1_outcome'])
        self.assertEqual(rows[0]['status'], 'UNKNOWN')
        credit(probes); self.assertIsNotNone(probes.next_stage())
        source['trace'] = source['trace'][:2]
        source['decision_evidence'] = source['decision_evidence'][:2]
        journal = ready()
        self.assertEqual(journal.observe(source, 'pre-combat-journal', 0, template()), [])

    def test_paired_same_baseline_reservation_reuses_five_base_results_no_extra_stage_one(self):
        probes = ready(); result, request = trajectory(), template()
        probes.observe(result, 'source', 0, request)
        specs = paired_specs(result, request, copy_baseline=True)
        self.assertEqual(probes.earmark_paired(specs), 1)
        credit(probes); self.assertIsNone(probes.next_stage())
        for spec in specs: probes.adopt_paired(spec, [loss(), win()])  # Added arm cannot make base viable.
        self.assertEqual(probes.entry_for_source('source')['status'], 'PENDING')
        stage = probes.next_stage()
        self.assertEqual(stage['stage'], 2)
        self.assertTrue(set(stage['samples']).isdisjoint(spec['sample'] for spec in specs))
        self.assertEqual(probes.snapshot()['counts']['scheduled_probes'], 5)
        self.assertEqual(probes.snapshot()['counts']['reused_paired_probes'], 5)

    def test_legacy_paired_without_baseline_cannot_reserve_explicit_owner_or_deadlock(self):
        probes = ready(); result, request = trajectory(), template()
        probes.observe(result, 'source', 0, request)
        specs = paired_specs(result, request)
        self.assertEqual(probes.earmark_paired(specs), 0)
        self.assertGreater(probes.rejections['paired_initial_baseline_mismatch'], 0)
        credit(probes); self.assertEqual(probes.next_stage()['stage'], 1)
        self.assertEqual(probes.adopt_paired(specs[0], [loss(), loss()]), [])

    def test_legacy_no_explicit_baseline_reuse_marks_limited_scope_and_missing_base_not_loss(self):
        probes = ready(); result, request = trajectory(), template(False)
        probes.observe(result, 'source', 0, request)
        specs = paired_specs(result, request)
        self.assertEqual(probes.earmark_paired(specs), 1)
        for index, spec in enumerate(specs): probes.adopt_paired(spec, [None if index == 0 else loss(), win()])
        row = probes.entry_for_source('source')
        self.assertEqual(row['status'], 'UNKNOWN')
        self.assertEqual(row['initial_context_scope'], 'legacy_no_explicit_progress_guard')
        credit(probes); self.assertIsNone(probes.next_stage())

    def test_scaled_all_groups_share_max_life_raw_data_and_unknown_work_stays_unknown(self):
        probes = ready(); request = template()
        probes.observe(trajectory(0, 'A'), 'one', 0, request)
        probes.observe(trajectory(1, 'B'), 'two', 0, request)
        known = {'measured_expanded_combat_nodes': 50, 'measured_probe_search_seconds': 1.0,
                 'measured_probe_native_seconds': 2.0, 'unknown_work_probes': 0}
        credit(probes); probes.absorb(probes.next_stage(), [loss(50)] * 4 + [win()], known, label='batch-one')
        credit(probes, offset=1)
        probes.absorb(probes.next_stage(), [loss(100, 200)] * 5, known, label='batch-two')
        self.assertEqual(probes.entry_for_source('one')['mean'], 0.5)
        self.assertEqual(probes.model_entries()[0]['f2_outcomes'][0]['lost'], (50.0, 100.0))
        report = probes.snapshot(total_native_seconds=100, total_native_work_complete=True)
        self.assertEqual(report['worker_share'], 0.04)
        self.assertEqual(report['measured_expanded_combat_nodes'], 100)
        self.assertEqual(report['f2_life_total'], 200)
        json.dumps(report)  # JSON telemetry must not expose bytes identity keys.
        fresh = ready(); fresh.observe(trajectory(), 'unknown', 0, request); credit(fresh)
        fresh.absorb(fresh.next_stage(), [loss()] * 5)
        self.assertIsNone(fresh.snapshot()['worker_share'])
        self.assertEqual(fresh.snapshot()['unknown_work_probes'], 5)

    def test_strict_raw_reducer_checks_observed_prefix_consumption_and_won_type(self):
        probes = ready(); source = trajectory(); probes.observe(source, 'source', 0, template()); credit(probes)
        request = probes.next_stage()['requests'][0]
        raw = deepcopy(source)
        raw.update(status='PROBE', synthetic=True, value=None, native_terminal_observed=False,
                   consumed=len(request['history']), reason=None,
                   probe={'entered': True, 'fought': True, 'won': False,
                          'entry_index': len(request['history']), 'edits': []})
        raw['trace'][len(request['history'])] = {'kind': 'probe_enter', 'encounter': 'F2'}
        raw['research_progress'] = {'schema': 'spire-research-progress/v1',
                                    'baseline_sha256': request['research_progress']['baseline_sha256'],
                                    'checkpoint_restored': False}
        self.assertIsNotNone(readiness_outcome(raw, request))
        for mutate in (lambda d: d.update(consumed=0), lambda d: d['trace'][0].update(index=9),
                       lambda d: d['probe'].update(won='false'), lambda d: d.update(reason='TIMEOUT')):
            bad = deepcopy(raw); mutate(bad)
            self.assertIsNone(readiness_outcome(bad, request))

    def test_larger_real_f2_life_total_rescales_mean_without_changing_readiness(self):
        for outcomes, state in (([loss()] * 5, 'DEAD_AT_FULL_HP'), ([loss()] * 4 + [win()], 'VIABLE')):
            with self.subTest(state=state):
                probes = ready(); probes.observe(trajectory(), 'source', 0, template()); credit(probes)
                probes.absorb(probes.next_stage(), outcomes)
                if state == 'DEAD_AT_FULL_HP':
                    credit(probes, offset=1); probes.absorb(probes.next_stage(), [loss()] * 5)
                before = probes.entry_for_source('source')
                self.assertEqual(before['status'], state)
                probes.set_f2_life_total(400)
                after = probes.entry_for_source('source')
                self.assertEqual(after['status'], before['status'])
                self.assertLess(after['mean'], before['mean'])
                self.assertEqual(after['f2_outcomes'], before['f2_outcomes'])
                probes.set_f2_life_total(float('nan')); probes.set_f2_life_total(True); probes.set_f2_life_total(0)
                self.assertEqual(probes.f2_life_total, 400)


if __name__ == '__main__':
    unittest.main()
