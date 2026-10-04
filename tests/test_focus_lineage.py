"""Reversible lineage allocation; fake native evidence, no game processes."""
import copy
import unittest

from spire_exact.canonical import canonical
from spire_exact.planning.focus import FocusScheduler
from spire_exact.planning.lineage import LineageBackoff


def campaign(*, lineage=0, first_route=0, variant=0, stage='f1', seconds=None,
             selection=0, boss_reward=0):
    trace, evidence = [], []

    def menu(act, floor, phase, choice=0, **extra):
        actions = [{'kind': phase, 'index': index} for index in range(3)]
        trace.append(actions[choice])
        evidence.append({'phase': phase, 'available_actions': actions,
                         'observation': {'act': act, 'floor': floor, 'hp': '80',
                                         'max_hp': '80', **extra}})

    def fight(act, floor, room='Boss'):
        trace.append({'kind': 'end_turn'})
        evidence.append({'phase': 'combat', 'available_actions': [{'kind': 'end_turn'}],
                         'observation': {'act': act, 'floor': floor, 'room': room,
                                         'turn': 1, 'hp': '80', 'max_hp': '80',
                                         'enemies': [{'id': 'MONSTER', 'combat_id': floor, 'hp': '100'}]}})

    menu(0, 1, 'map', first_route)
    menu(0, 8, 'select_cards', selection, selection={'purpose': 'Remove'})
    fight(0, 17)
    menu(0, 17, 'rewards')
    menu(1, 18, 'map', lineage)
    if stage in ('early', 'second_boss'):
        floor, room = (26, 'Monster') if stage == 'early' else (33, 'Boss')
        fight(1, floor, room)
        act = 1
    else:
        fight(1, 33)
        menu(1, 33, 'card_reward', boss_reward)
        menu(2, 34, 'map')
        menu(2, 36, 'card_reward', variant)
        if stage == 'corridor':
            fight(2, 46, 'Monster')
            act, floor, room = 2, 46, 'Monster'
        else:
            fight(2, 48)
            if stage == 'f2':
                menu(2, 48, 'map')
                fight(2, 49)
                floor = 49
            else:
                floor = 48
            act, room = 2, 'Boss'
    terminal = {'act': act, 'floor': floor, 'room': room, 'hp': '0', 'max_hp': '80',
                'turn': 5, 'enemies': [{'id': 'MONSTER', 'combat_id': floor, 'hp': str(70 + variant)}]}
    result = {'status': 'TERMINAL', 'value': [0], 'trace': trace, 'decision_evidence': evidence,
              'observation': terminal, 'terminal_combat': copy.deepcopy(terminal)}
    if seconds is not None:
        result['completed_wall_seconds'] = seconds
    return result


def early_spec(source):
    return {'kind': 'macro_focus', 'group': {'source': source, 'index': 4, 'act': 1}}


class LineageIdentityTests(unittest.TestCase):
    def test_combat_and_third_act_variants_do_not_create_a_new_lineage(self):
        tracker = LineageBackoff(3, enabled=True)
        a = campaign(variant=0)
        b = campaign(variant=1)
        b['trace'][2] = {'kind': 'end_turn', 'combat_variant': True}
        tracker.observe(a, 'a')
        tracker.observe(b, 'b')
        self.assertEqual(tracker.snapshot()['act3_lineages'], 1)
        self.assertEqual(len(next(iter(tracker.lineages.values())).gates[(2, 0)][0]), 2)

    def test_noncombat_select_cards_and_second_boss_rewards_are_in_the_lineage(self):
        tracker = LineageBackoff(4)
        tracker.observe(campaign(selection=0), 'a')
        tracker.observe(campaign(selection=1), 'b')
        tracker.observe(campaign(boss_reward=1), 'c')
        self.assertEqual(tracker.snapshot()['act3_lineages'], 3)
        self.assertEqual(tracker.snapshot()['first_act_routes'], 1)

    def test_missing_act_or_selection_semantics_stays_unknown(self):
        tracker = LineageBackoff(1, enabled=True)
        result = campaign()
        result['decision_evidence'][0]['observation'].pop('act')
        tracker.observe(result, 'missing-act')
        result = campaign()
        result['decision_evidence'][1]['observation'].pop('selection')
        tracker.observe(result, 'missing-purpose')
        snapshot = tracker.snapshot()
        self.assertIsNone(snapshot['act3_lineages'])
        self.assertIsNone(snapshot['first_act_routes'])
        self.assertEqual(snapshot['unknown_lineage_observations'], 2)
        self.assertEqual(snapshot['observed_act3_lineages'], 0)
        self.assertIsNone(snapshot['target_act'])

    def test_unknown_resource_synthetic_and_cached_work_neither_counts_nor_restores(self):
        tracker = LineageBackoff(2, enabled=True)
        tracker.observe(campaign(), 'a')
        tracker.observe(campaign(variant=1), 'b')
        for label, edits, spec in (
            ('unknown', {'status': 'UNKNOWN', 'value': None}, None),
            ('oom', {'status': 'UNKNOWN', 'reason': 'NATIVE_TASK_OUT_OF_MEMORY'}, None),
            ('synthetic', {'synthetic': True}, None),
            ('cache', {}, {'cache_hit': True}),
        ):
            result = campaign(lineage=1, stage='f2')
            result.update(edits)
            tracker.observe(result, label, spec)
        self.assertEqual(tracker.snapshot()['act3_lineages'], 1)
        self.assertEqual(tracker.snapshot()['target_act'], 1)
        self.assertEqual(len(next(iter(tracker.lineages.values())).gates[(2, 0)][0]), 2)


class LineageBackoffTests(unittest.TestCase):
    def stalled(self, *, stage='f1'):
        scheduler = FocusScheduler(explore=0, stall=2, stall_extended=True)
        scheduler.add(campaign(stage=stage, seconds=10), 'a')
        scheduler.add(campaign(stage=stage, variant=1, seconds=20), 'b')
        return scheduler

    def test_distinct_boss_entries_pause_third_act_and_alternate_pre_second_boss_picks(self):
        scheduler = self.stalled()
        snapshot = scheduler.snapshot()['focus_stall_extended']
        self.assertEqual(snapshot['target_act'], 1)
        gate = next(row for row in snapshot['paused_lineages'][0]['gates'] if row['gate'] == [2, 0])
        self.assertEqual(gate['entries'], 2)
        usual = scheduler.next()
        self.assertNotIn('lineage_backoff', usual)
        self.assertLessEqual(usual['floor'], 33)
        proposal = scheduler.next()
        self.assertEqual((proposal['act'], proposal['floor']), (1, 18))
        self.assertTrue(proposal['lineage_backoff'])
        self.assertLess(proposal['index'], scheduler.lineage.labels[proposal['source']].boss_entries[1])
        self.assertTrue(scheduler.preparation_blocked('a'))
        self.assertTrue(scheduler.preparation_blocked({'preparation': {'source': 'a'}}))
        self.assertTrue(scheduler.dispatch_blocked({'group': {'source': 'a', 'act': 2}}))
        self.assertFalse(scheduler.dispatch_blocked(early_spec('a')))

    def test_second_act_distinct_boss_stalls_step_back_to_first_act(self):
        scheduler = self.stalled()
        scheduler.add(campaign(stage='early', lineage=1, seconds=25), 'early-a', early_spec('a'))
        self.assertEqual(scheduler.lineage.target_act(), 1)
        scheduler.add(campaign(stage='second_boss', lineage=1, seconds=27), 'boss-a', early_spec('a'))
        self.assertEqual(scheduler.lineage.target_act(), 1)
        scheduler.add(campaign(stage='second_boss', lineage=2, seconds=30), 'boss-b', early_spec('a'))
        self.assertEqual(scheduler.lineage.target_act(), 0)
        proposal = scheduler._backoff_next()
        self.assertEqual(proposal['act'], 0)
        self.assertLess(proposal['index'], scheduler.lineage.labels[proposal['source']].boss_entries[0])
        events = scheduler.snapshot()['focus_stall_extended']['events']
        self.assertEqual([row['target_act'] for row in events if row['event'] == 'lineage_backoff'], [1, 0])

    def test_corridor_deaths_and_repeated_exact_boss_entries_do_not_trigger(self):
        scheduler = FocusScheduler(stall=2, stall_extended=True)
        scheduler.add(campaign(stage='corridor'), 'hall-a')
        scheduler.add(campaign(stage='corridor', variant=1), 'hall-b')
        self.assertIsNone(scheduler.lineage.target_act())
        scheduler.add(campaign(), 'boss-a')
        scheduler.add(campaign(), 'same-entry-repair', {'kind': 'gate_retry', 'repair': {'source': 'boss-a'}})
        self.assertIsNone(scheduler.lineage.target_act())
        scheduler.add(campaign(variant=1), 'boss-b')
        self.assertEqual(scheduler.lineage.target_act(), 1)

    def test_half_alternation_and_explorer_share_remain_original(self):
        scheduler = self.stalled()
        picks = [scheduler.next() for _ in range(4)]
        self.assertEqual(['lineage_backoff' in row for row in picks], [False, True, False, True])
        self.assertEqual(scheduler.focus_picks, 4)
        self.assertEqual(scheduler.explore_picks, 0)
        report = scheduler.snapshot()['focus_stall_extended']
        self.assertEqual((report['step_back_evaluations'], report['usual_evaluations_while_stalled']), (2, 2))

    def test_backoff_prefers_an_unseen_pre_third_act_stem(self):
        scheduler = FocusScheduler(explore=0, stall=1, stall_extended=True)
        scheduler.add(campaign(lineage=0), 'a')
        scheduler.add(campaign(lineage=1), 'b')
        proposal = scheduler._backoff_next()
        self.assertEqual((proposal['act'], proposal['floor'], proposal['prefix'][-1]['index']), (1, 18, 2))

    def test_consumer_and_probe_sources_are_explicit_and_unknown_sources_are_not_guessed(self):
        scheduler = self.stalled()
        for spec in ({'kind': 'deepen', 'source': 'a'},
                     {'kind': 'memory_prefix_followup', 'memory_prefix_source': 'a'},
                     {'kind': 'f1_winner_reuse', 'repair': {'source': 'a'}},
                     {'kind': 'real_card_menu_choice', 'source': 'a'},
                     {'kind': 'real_card_menu_probe', 'base': 'a', 'gate': (2, 'card_menu')},
                     {'kind': 'paired_card_probe', 'base': 'a', 'gate': (2, 1)}):
            with self.subTest(spec=spec):
                self.assertTrue(scheduler.dispatch_blocked(spec))
        self.assertFalse(scheduler.dispatch_blocked({'kind': 'deepen', 'family': 'a'}))
        self.assertFalse(scheduler.dispatch_blocked({'kind': 'unrelated', 'base': 'a'}))

    def test_a_new_lineage_act3_entry_restores_existing_pauses(self):
        scheduler = self.stalled()
        late = {key for key, branch in scheduler.explorer.branches.items() if branch.act == 2}
        scheduler.add(campaign(lineage=1, seconds=40), 'new-lineage')
        self.assertFalse(scheduler.preparation_blocked('a'))
        self.assertIsNone(scheduler.lineage.target_act())
        self.assertTrue(late & scheduler.explorer.branches.keys())
        trigger = next(row for row in scheduler.lineage.events if row['event'] == 'lineage_backoff')
        self.assertEqual(trigger['first_new_lineage_act3_seconds'], 40)

    def test_new_f1_pass_restores_and_f2_never_stalls_or_blocks_preparation(self):
        scheduler = self.stalled(stage='f1')
        scheduler.add(campaign(stage='f2', seconds=40), 'passed-f1')
        self.assertFalse(scheduler.lineage.preparation_blocked('a'))
        self.assertIsNone(scheduler.lineage.target_act())
        scheduler.add(campaign(stage='f2', variant=1, seconds=45), 'f2-fail-one')
        scheduler.add(campaign(stage='f2', variant=2, seconds=50), 'f2-fail-two')
        self.assertIsNone(scheduler.lineage.target_act())
        self.assertFalse(scheduler.preparation_blocked('a'))
        self.assertFalse(scheduler.dispatch_blocked({'kind': 'deepen', 'source': 'a'}))
        self.assertFalse(scheduler.snapshot()['focus_stall_extended']['f2_backoff_enabled'])

    def test_second_act_stalls_without_any_lineage_having_entered_act3(self):
        scheduler = FocusScheduler(explore=0, stall=2, stall_extended=True)
        a = campaign(stage='second_boss')
        b = copy.deepcopy(a)
        # Same observed non-combat prefix; a different genuine battle history
        # supplies a distinct entry to the second-act first boss.
        b['trace'][2] = {'kind': 'end_turn', 'combat_variant': True}
        scheduler.add(a, 'second-boss-a')
        self.assertIsNone(scheduler.lineage.target_act())
        scheduler.add(b, 'second-boss-b')
        self.assertEqual(scheduler.lineage.target_act(), 0)
        self.assertTrue(scheduler.preparation_blocked('second-boss-a'))
        self.assertTrue(scheduler.dispatch_blocked({'group': {'source': 'second-boss-a', 'act': 1}}))
        self.assertEqual(scheduler.snapshot()['focus_stall_extended']['act3_lineages'], 0)
        scheduler.next()  # Usual half, using the source's earlier decisions.
        back = scheduler.next()
        self.assertEqual(back['act'], 0)
        self.assertLess(back['index'], scheduler.lineage.labels[back['source']].boss_entries[0])

    def test_separate_f2_switch_counts_distinct_entries_not_same_entry_A_reuse(self):
        scheduler = FocusScheduler(explore=0, stall=2, stall_extended=True, stall_f2=True)
        scheduler.add(campaign(stage='f2'), 'f2-first')
        self.assertIsNone(scheduler.lineage.target_act())
        scheduler.add(campaign(stage='f2'), 'A-same-entry',
                      {'kind': 'f1_winner_reuse', 'repair': {'source': 'f2-first'}})
        self.assertIsNone(scheduler.lineage.target_act())
        scheduler.add(campaign(stage='f2', variant=1), 'f2-new-entry')
        self.assertEqual(scheduler.lineage.target_act(), 1)
        self.assertTrue(scheduler.preparation_blocked('f2-first'))
        report = scheduler.snapshot()['focus_stall_extended']
        self.assertTrue(report['f2_backoff_enabled'])
        self.assertEqual(report['gate_scope'], [[1, 0], [2, 0], [2, 1]])
        event = next(row for row in report['events'] if row['event'] == 'lineage_backoff')
        self.assertEqual((event['stalled_gate'], event['distinct_entries']), ([2, 1], 2))
        normal, back = scheduler.next(), scheduler.next()
        self.assertNotIn('lineage_backoff', normal)
        self.assertTrue(back['lineage_backoff'])
        self.assertEqual(back['act'], 1)

    def test_a_real_f2_pass_restores_separately_enabled_f2_stall(self):
        scheduler = FocusScheduler(stall=2, stall_extended=True, stall_f2=True)
        scheduler.add(campaign(stage='f2'), 'a')
        scheduler.add(campaign(stage='f2', variant=1), 'b')
        winner = campaign(stage='f2', variant=1)
        winner.update(value=[1])
        winner['observation'].update(hp='40', enemies=[])
        winner['observation'].pop('turn')
        scheduler.add(winner, 'f2-winner')
        self.assertIsNone(scheduler.lineage.target_act())
        self.assertFalse(scheduler.preparation_blocked('a'))

    def test_explicit_f2_off_matches_the_default_without_altering_normal_requests(self):
        implicit = FocusScheduler(stall=2, stall_extended=True)
        explicit = FocusScheduler(stall=2, stall_extended=True, stall_f2=False)
        for index in range(3):
            result = campaign(stage='f2', variant=index)
            implicit.add(result, str(index))
            explicit.add(copy.deepcopy(result), str(index))
        left, right = [], []
        while (group := implicit.next()) is not None:
            left.append(canonical(group))
        while (group := explicit.next()) is not None:
            right.append(canonical(group))
        self.assertEqual(left, right)
        self.assertIsNone(explicit.lineage.target_act())

    def test_f2_switch_requires_extended_positive_threshold_and_boolean_value(self):
        for options in ({'stall_f2': True},
                        {'stall_f2': True, 'stall': 2},
                        {'stall_f2': True, 'stall_extended': True},
                        {'stall_f2': 1, 'stall_extended': True, 'stall': 2}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                FocusScheduler(**options)

    def test_paused_branches_remain_pending_when_no_earlier_proposal_exists(self):
        root = campaign(stage='f1')
        scope = root['trace'][:8]
        scheduler = FocusScheduler(scope, explore=0, stall=2, stall_extended=True)
        scheduler.add(root, 'a')
        variant = campaign(stage='f1', variant=1)
        scheduler.add(variant, 'b')
        pending = dict(scheduler.explorer.branches)
        self.assertIsNone(scheduler.next())
        self.assertEqual(scheduler.explorer.branches, pending)
        self.assertTrue(scheduler.has_blocked_pending())

    def test_missing_native_boss_boundary_does_not_guess_from_floor_numbers(self):
        scheduler = self.stalled()
        for observation in scheduler.lineage.labels.values():
            observation.boss_entries.clear()
        pending = dict(scheduler.explorer.branches)
        self.assertIsNone(scheduler._backoff_next())
        self.assertEqual(scheduler.explorer.branches, pending)

    def test_zero_existing_threshold_keeps_lineage_backoff_inactive(self):
        scheduler = FocusScheduler(stall=0, stall_extended=True)
        for index in range(5):
            scheduler.add(campaign(variant=index % 3), str(index))
        self.assertIsNone(scheduler.lineage.target_act())
        self.assertFalse(scheduler.preparation_blocked('0'))

    def test_explicit_off_produces_byte_identical_existing_requests(self):
        for stall in (0, 2):
            with self.subTest(stall=stall):
                default = FocusScheduler(stall=stall)
                off = FocusScheduler(stall=stall, stall_extended=False)
                for index in range(3):
                    result = campaign(variant=index)
                    default.add(result, str(index))
                    off.add(copy.deepcopy(result), str(index), {'group': {'source': 'ignored'}},
                            completed_wall_seconds=100)
                actual, expected = [], []
                while (proposal := default.next()) is not None:
                    expected.append(canonical(proposal))
                while (proposal := off.next()) is not None:
                    actual.append(canonical(proposal))
                self.assertEqual(actual, expected)
                self.assertNotIn('focus_stall_extended', off.snapshot())

    def test_legacy_first_boss_stall_can_defer_preparation_without_changing_focus_order(self):
        scheduler = FocusScheduler(stall=2)
        # Distinct F1 entries reach the old global stall, independently of D.
        scheduler.add(campaign(variant=0), 'a')
        scheduler.add(campaign(variant=1), 'b')
        self.assertTrue(scheduler.preparation_blocked('a'))
        self.assertFalse(scheduler.dispatch_blocked({'group': {'source': 'a', 'act': 2}}))
        self.assertNotIn('focus_stall_extended', scheduler.snapshot())

    def test_invalid_switch_is_rejected(self):
        with self.assertRaises(ValueError):
            FocusScheduler(stall_extended=1)


class LineageTelemetryTests(unittest.TestCase):
    def test_five_minute_samples_count_actual_routes_and_keep_missing_time_unknown(self):
        tracker = LineageBackoff(2, enabled=True)
        tracker.observe(campaign(), 'a')
        self.assertIsNone(tracker.snapshot()['completed_wall_seconds'])
        tracker.observe(campaign(variant=1, seconds=301), 'b')
        tracker.observe(campaign(lineage=1, seconds=610), 'c')
        tracker.observe(campaign(first_route=1, seconds=901), 'd')
        windows = tracker.snapshot()['five_minute_observations']
        self.assertEqual([row['interval_end_seconds'] for row in windows], [300, 600, 900])
        self.assertEqual([row['act3_lineages'] for row in windows], [1, 2, 3])
        self.assertEqual([row['first_act_routes'] for row in windows], [1, 1, 2])
        self.assertIsNone(tracker.events[0]['completed_wall_seconds'])


if __name__ == '__main__':
    unittest.main()
