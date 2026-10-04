"""Semantic scheduling/diagnostic checks using reference protocol fixtures.

No native assembly, game, solver, probe or fresh-win verifier is started here.
"""
from copy import deepcopy
import unittest
from spire_exact.canonical import canonical
from spire_exact.planning.f1_winners import F1WinnerReuse


NATIVE_ID = {'host_sha256': 'host', 'game_sha256': 'game', 'harmony_sha256': 'harmony'}
ADVISOR_ID = {'solver.dll': 'solver-hash', 'harness.dll': 'harness-hash'}
CONTEXT = {'seed': '42', 'character': 'IRONCLAD', 'ascension': 10, 'unlocks': 'all',
           'information': 'full', 'objective': 'whole_run_victory/v1'}
TEMPLATE = {**{key: CONTEXT[key] for key in ('seed', 'character', 'ascension', 'unlocks')},
            'history': [], 'generate_candidate': True, 'max_decisions': 12000,
            'capture_checkpoints': True, 'checkpoint': 'source-only-checkpoint',
            'policy_seed': 7, 'policy_prior': {'card:ROOT': [1, 1, 1]},
            'advisor': {'binary_identity': ADVISOR_ID, 'nodes': 60000,
                        'budget_ms': 600000, 'boss_budget_ms': 600000}}


def artifact(*, selected=False, member=1, plan='STRIKE'):
    return {'schema': 'spire-f1-winner/v1', 'selected': selected, 'forecast': {'won': True},
            'forecast_native_json': '{"hp":42.5}',
            'entry_history': [{'kind': 'event', 'index': 0}, {'kind': 'map', 'col': 1, 'row': 15}],
            'context': deepcopy(CONTEXT), 'native_identity': deepcopy(NATIVE_ID),
            'advisor_binary_identity': deepcopy(ADVISOR_ID),
            'entry_observation': {'hp': '42.5', 'rng': {'counter': 37}, 'potions': ['P']},
            'native_progress': {'normalization_schema': 'fixture/guard',
                                'native_json': '{"discovered_enemies":["F1"],"runs":2}'},
            'native_progress_raw': '{"unique_id":"random-profile","discovered_enemies":["F1"]}',
            'root_state_text': 'all-live-root-fields;piles=ACD;rng=37',
            'encounter': 'F1', 'entry_turn': 1, 'member_index': member,
            'member': {'mode': 'Evaluate', 'beam': 68},
            'route_actions': [{'Kind': 'PlayCard', 'Turn': 1, 'CardId': plan,
                              'CardOccurrence': 0, 'CardStateKey': 'complete-card-state',
                              'TargetCombatId': 12, 'Choice': None,
                              'NestedChoices': [{'Effect': 'Discard', 'SourcePile': 'Hand',
                                  'Cards': [{'CardId': 'C', 'OptionOccurrence': 0,
                                             'StateKey': 'full-C-state'}]}],
                              'NestedChoicesBeforePrimary': 0,
                              'TurnStartChoices': [{'Effect': 'MoveToHand', 'Cards': []}]},
                             {'Kind': 'EndTurn', 'Turn': 1}],
            'continuations': [{'StateText': 'all-turn2-fields;piles=CAD;rng=41',
                               'StartTurnNumber': 2, 'ForecastOffset': 2}]}


def source_result(rows=None):
    rows = rows if rows is not None else [artifact()]
    return {'status': 'TERMINAL', 'value': [0, 123], 'reason': None,
            'native_terminal_observed': False,
            'trace': deepcopy(rows[0]['entry_history']) + [{'kind': 'end_turn'}, {'kind': 'map', 'col': 0, 'row': 16}],
            'f1_winner_candidates': deepcopy(rows),
            'f1_winner_reuse': {'terminal_encounter': 'F2', 'final_second_encounter': 'F2',
                'native_boss_wins': ['F1'], 'native_identity': deepcopy(NATIVE_ID),
                'deployment': {'captured_f1_root_count': 1, 'loaded': False}}}


class F1WinnerReuseTests(unittest.TestCase):
    def test_source_progress_baseline_is_bound_and_copied_without_using_expected_guard(self):
        template=deepcopy(TEMPLATE)
        template['research_progress']={'schema':'fixture/v1','baseline_snapshot':'native initial bytes'}
        source=deepcopy(template)
        specs=F1WinnerReuse(True).observe(source_result(),'source',template,source_request=source)
        self.assertEqual(len(specs),1)
        self.assertEqual(specs[0]['request']['research_progress'],source['research_progress'])
        self.assertNotEqual(specs[0]['request']['research_progress']['baseline_snapshot'],artifact()['native_progress_raw'])
        source['research_progress']['baseline_snapshot']='other native initial bytes'
        manager=F1WinnerReuse(True)
        self.assertEqual(manager.observe(source_result(),'source',template,source_request=source),[])
        self.assertEqual(manager.snapshot()['artifact_rejection_reasons'],{'source_progress_contract':1})

    def test_default_off_never_proposes_or_mutates_request(self):
        template = deepcopy(TEMPLATE); before = canonical(template)
        manager = F1WinnerReuse()
        self.assertEqual(manager.observe(source_result(), 'source', template), [])
        self.assertEqual(canonical(template), before)
        self.assertEqual(manager.snapshot()['alternative_requests'], 0)

    def test_only_observed_real_second_boss_route_death_triggers(self):
        base = source_result()
        for change in ({'status': 'UNKNOWN'}, {'status': 'UNSUPPORTED'}, {'status': 'BUDGET'},
                       {'value': None}, {'value': [1]}, {'value': [False]}, {'value': []},
                       {'reason': 'NATIVE_TASK_TIMEOUT'}, {'synthetic': True}):
            with self.subTest(change=change):
                result = deepcopy(base); result.update(change)
                self.assertEqual(F1WinnerReuse(True).observe(result, 'source', TEMPLATE), [])
        for terminal in ('F1', 'ordinary-enemy', None):
            result = deepcopy(base); result['f1_winner_reuse']['terminal_encounter'] = terminal
            self.assertEqual(F1WinnerReuse(True).observe(result, 'source', TEMPLATE), [])
        manager = F1WinnerReuse(True)
        self.assertEqual(len(manager.observe(base, 'source', TEMPLATE)), 1)
        self.assertEqual(manager.snapshot()['observed_real_f2_deaths'], 1)

    def test_unobserved_first_boss_win_cannot_supply_a_reuse(self):
        result = source_result(); result['f1_winner_reuse']['native_boss_wins'] = []
        manager = F1WinnerReuse(True)
        self.assertEqual(manager.observe(result, 'source', TEMPLATE), [])
        self.assertEqual(manager.snapshot()['artifact_rejection_reasons'], {'source_f1_win_not_observed': 1})

    def test_selected_member_is_reported_but_not_scheduled(self):
        manager = F1WinnerReuse(True)
        self.assertEqual(manager.observe(source_result([artifact(selected=True)]), 'source', TEMPLATE), [])
        stats = manager.snapshot()
        self.assertEqual(stats['captured_predicted_winner_members'], 1)
        self.assertEqual(stats['selected_predicted_winner_members'], 1)
        self.assertEqual(stats['selected_full_plans_marked_used'], 1)

    def test_selected_complete_plan_marks_identical_unselected_route_used(self):
        # Selected is deliberately later in member order: marking must happen
        # before scheduling the earlier unselected identical complete plan.
        unused = artifact(member=1); selected = artifact(selected=True, member=9)
        manager = F1WinnerReuse(True)
        self.assertEqual(manager.observe(source_result([unused, selected]), 'source', TEMPLATE), [])
        self.assertEqual(manager.snapshot()['duplicate_plans'], 1)

    def test_dedup_uses_full_bytes_not_member_index_or_audit_forecast(self):
        result = source_result(); manager = F1WinnerReuse(True)
        self.assertEqual(len(manager.observe(result, 'source-a', TEMPLATE)), 1)
        clone = deepcopy(result); clone['f1_winner_candidates'][0]['member_index'] = 135
        clone['f1_winner_candidates'][0]['forecast_native_json'] = '{"hp":12.125}'
        clone['f1_winner_candidates'][0]['native_progress_raw'] = '{"unique_id":"different-profile"}'
        self.assertEqual(manager.observe(clone, 'source-b', TEMPLATE), [])
        self.assertEqual(manager.snapshot()['duplicate_plans'], 1)

    def test_nested_choice_continuation_rng_and_card_instance_remain_in_plan_key(self):
        manager = F1WinnerReuse(True); base = source_result()
        self.assertEqual(len(manager.observe(base, 'source-0', TEMPLATE)), 1)
        for index, change in enumerate(('nested', 'turn_start', 'continuation', 'root_rng', 'instance', 'progress')):
            result = deepcopy(base); proposal = result['f1_winner_candidates'][0]
            if change == 'nested': proposal['route_actions'][0]['NestedChoices'][0]['Cards'][0]['OptionOccurrence'] = 1
            elif change == 'turn_start': proposal['route_actions'][0]['TurnStartChoices'][0]['Cards'] = [{'CardId': 'NEW'}]
            elif change == 'continuation': proposal['continuations'][0]['StateText'] += ';extra-counter=1'
            elif change == 'root_rng': proposal['root_state_text'] += ';extra-rng=1'
            elif change == 'instance': proposal['route_actions'][0]['CardOccurrence'] = 1
            elif change == 'progress': proposal['native_progress']['native_json'] = '{"discovered_enemies":["F1","NEW"]}'
            with self.subTest(change=change):
                self.assertEqual(len(manager.observe(result, f'source-{index+1}', TEMPLATE)), 1)

    def test_prefix_context_native_and_advisor_identity_mismatches_are_rejected(self):
        for change in ('prefix', 'context', 'native_id', 'advisor_id'):
            result = source_result(); proposal = result['f1_winner_candidates'][0]
            if change == 'prefix': proposal['entry_history'][0]['index'] = 1
            elif change == 'context': proposal['context']['seed'] = '43'
            elif change == 'native_id': proposal['native_identity']['game_sha256'] = 'changed'
            elif change == 'advisor_id': proposal['advisor_binary_identity']['solver.dll'] = 'changed'
            with self.subTest(change=change):
                manager = F1WinnerReuse(True)
                self.assertEqual(manager.observe(result, 'source', TEMPLATE), [])
                self.assertEqual(manager.snapshot()['skipped_invalid_artifacts'], 1)

    def test_real_source_policy_prior_and_tactical_settings_are_deep_copied(self):
        root = deepcopy(TEMPLATE); source = deepcopy(TEMPLATE)
        source.update(policy_seed=93, policy_prior={'card:SOURCE': [0, -2, 4]})
        source['advisor'].update(nodes=540000, gate_plans={'FinalBoss': {'members': [{'beam': 270}]}})
        before = canonical(root)
        specs = F1WinnerReuse(True).observe(source_result(), 'source', root, source_request=source)
        request = specs[0]['request']
        self.assertEqual(request['policy_seed'], 93)
        self.assertEqual(request['policy_prior'], source['policy_prior'])
        self.assertEqual(request['advisor'], source['advisor'])
        self.assertEqual(canonical(root), before)
        source['advisor']['nodes'] = 1; source['policy_prior']['card:SOURCE'][0] = 8
        self.assertEqual(request['advisor']['nodes'], 540000)
        self.assertEqual(request['policy_prior']['card:SOURCE'], [0, -2, 4])

    def test_source_without_prior_does_not_inherit_root_prior(self):
        source = deepcopy(TEMPLATE); source.pop('policy_prior')
        request = F1WinnerReuse(True).observe(source_result(), 'source', TEMPLATE, source_request=source)[0]['request']
        self.assertNotIn('policy_prior', request)

    def test_wrong_source_context_or_binary_cannot_replace_root_binding(self):
        for change in ('seed', 'binary'):
            source = deepcopy(TEMPLATE)
            if change == 'seed': source['seed'] = '43'
            else: source['advisor']['binary_identity']['solver.dll'] = 'changed'
            self.assertEqual(F1WinnerReuse(True).observe(source_result(), 'source', TEMPLATE, source_request=source), [])

    def test_consumers_strip_checkpoint_and_synthetic_controls(self):
        template = deepcopy(TEMPLATE)
        template.update(probe={'unsafe': True}, card_menu_probe={}, real_card_menu_choice={},
                        expected_evidence=[], stop_at_strategic_decision=True, stop_at_floor=99)
        result = source_result()
        request = F1WinnerReuse(True).observe(result, 'source', template)[0]['request']
        self.assertFalse(request['capture_checkpoints'])
        for name in ('checkpoint', 'probe', 'card_menu_probe', 'real_card_menu_choice',
                     'expected_evidence', 'stop_at_strategic_decision', 'stop_at_floor'):
            self.assertNotIn(name, request)
        self.assertEqual(request['history'], result['f1_winner_candidates'][0]['entry_history'])
        canonical(request)  # Fractional native forecast audit remains a string.

    def test_proposals_have_deterministic_member_order(self):
        rows = [artifact(member=3, plan='C'), artifact(member=1, plan='A'), artifact(member=2, plan='B')]
        specs = F1WinnerReuse(True).observe(source_result(rows), 'source', TEMPLATE)
        self.assertEqual([spec['repair']['member_index'] for spec in specs], [1, 2, 3])

    def test_fractional_native_identity_data_is_rejected_not_rounded(self):
        result = source_result(); result['f1_winner_candidates'][0]['entry_observation']['hp'] = 42.5
        manager = F1WinnerReuse(True)
        self.assertEqual(manager.observe(result, 'source', TEMPLATE), [])
        self.assertEqual(manager.snapshot()['artifact_rejection_reasons'], {'artifact_contract': 1})
        self.assertFalse(manager.snapshot()['native_state_equivalence_proven'])

    def test_loading_a_forecast_without_native_win_callback_is_not_deployment_success(self):
        manager = F1WinnerReuse(True)
        result = {'status': 'BUDGET', 'value': None, 'reason': 'candidate_decision_budget',
                  'f1_winner_reuse': {'guard_attempted': True, 'proposal_entry_checked': True,
                    'native_f1_won': False, 'native_f2_entered': False, 'deployment': {'loaded': True}}}
        manager.record_execution(result, 'consumer')
        stats = manager.snapshot()
        self.assertEqual(stats['consumer_routes_loaded'], 1)
        self.assertEqual(stats['consumer_native_f1_wins'], 0)
        self.assertEqual(stats['consumer_unknown'], 1)
        self.assertEqual(stats['consumer_unknown_reasons'], {'candidate_decision_budget': 1})

    def test_native_first_boss_win_and_second_boss_entry_are_counted_once(self):
        manager = F1WinnerReuse(True)
        result = {'status': 'TERMINAL', 'value': [0], 'reason': None,
                  'f1_winner_reuse': {'guard_attempted': True, 'proposal_entry_checked': True,
                    'native_f1_won': True, 'native_f2_entered': True, 'deployment': {'loaded': True}},
                  'advisor_metrics': {'searches': [{'encounter': 'F2', 'expanded_nodes': 30}]}}
        request = {'f1_winner_proposal': artifact()}
        manager.record_execution(result, 'consumer', request)
        manager.record_execution(result, 'consumer', request)
        stats = manager.snapshot()
        self.assertEqual(stats['consumer_evaluations'], 1)
        self.assertEqual(stats['consumer_native_f1_wins'], 1)
        self.assertEqual(stats['consumer_native_f2_entries'], 1)
        self.assertEqual(stats['consumer_unknown'], 0)
        self.assertEqual(stats['consumer_f1_search_audits_known'], 1)
        self.assertEqual(stats['consumer_f1_member_search_records'], 0)

    def test_progress_and_root_guard_mismatches_stay_unknown_with_reason_counts(self):
        manager = F1WinnerReuse(True)
        progress = {'status': 'UNSUPPORTED', 'value': None, 'reason': 'long native traceback',
                    'f1_winner_reuse': {'guard_attempted': True, 'proposal_entry_checked': False,
                        'failed_entry_guard': 'f1_winner_native_progress', 'deployment': {'loaded': False}}}
        root = {'status': 'UNSUPPORTED', 'value': None, 'reason': 'wrapper',
                'f1_winner_reuse': {'guard_attempted': True, 'proposal_entry_checked': True,
                    'deployment': {'loaded': False, 'rejection_reason': 'F1_WINNER_ROOT_STATE_MISMATCH'}}}
        manager.record_execution(progress, 'a'); manager.record_execution(root, 'b')
        stats = manager.snapshot()
        self.assertEqual(stats['consumer_unknown'], 2)
        self.assertEqual(stats['consumer_routes_loaded'], 0)
        self.assertEqual(stats['consumer_guard_mismatch_reasons'], {
            'CHECKPOINT_MISMATCH:f1_winner_native_progress': 1, 'F1_WINNER_ROOT_STATE_MISMATCH': 1})
        self.assertFalse(stats['native_state_equivalence_proven'])
        self.assertNotIn('infeasible', stats)

    def test_mapping_failure_and_worker_timeout_are_not_silent_zero_cost_successes(self):
        manager = F1WinnerReuse(True)
        mapping = {'status': 'UNSUPPORTED', 'value': None, 'reason': 'wrapper',
                   'f1_winner_reuse': {'deployment': {'loaded': True,
                       'rejection_reason': 'F1_WINNER_DEPLOYMENT_MISMATCH:nested_selection_identity_mismatch'}}}
        manager.record_execution(mapping, 'mapping')
        manager.record_execution({'status': 'UNKNOWN', 'reason': 'NATIVE_TASK_TIMEOUT'}, 'timeout')
        stats = manager.snapshot()
        self.assertEqual(stats['consumer_unknown'], 2)
        self.assertEqual(stats['consumer_f1_search_audits_unknown'], 2)
        self.assertEqual(stats['consumer_native_f1_wins'], 0)
        self.assertEqual(stats['consumer_unknown_reasons']['NATIVE_TASK_TIMEOUT'], 1)

    def test_a_real_f1_search_is_reported_instead_of_hardcoded_zero(self):
        manager = F1WinnerReuse(True)
        result = {'status': 'UNKNOWN', 'reason': 'cancelled',
                  'advisor_metrics': {'searches': [{'encounter': 'F1'}, {'encounter': 'F2'}]}}
        manager.record_execution(result, 'consumer', {'f1_winner_proposal': artifact()})
        self.assertEqual(manager.snapshot()['consumer_f1_member_search_records'], 1)

    def test_native_winning_candidate_is_not_counted_as_a_verified_certificate(self):
        manager = F1WinnerReuse(True)
        result = {'status': 'TERMINAL', 'value': [1], 'reason': None, 'native_terminal_observed': True}
        manager.record_execution(result, 'winner')
        stats = manager.snapshot()
        self.assertEqual(stats['consumer_native_win_candidates'], 1)
        self.assertNotIn('certified_wins', stats)
        self.assertNotIn('proven_optimal', stats)


if __name__ == '__main__': unittest.main()
