"""Pure F1 winner scheduling fixtures; no native process or solver is started."""
from copy import deepcopy
import unittest

from spire_exact.canonical import canonical
from spire_exact.planning.f1_winners import F1WinnerReuse
from tests.test_f1_winner_reuse import TEMPLATE, artifact, source_result


def winner(member, hp=None, *, selected=False, plan=None):
    row = artifact(member=member, selected=selected, plan=plan or f'PLAN-{member}')
    if hp is not None:
        row['forecast']['hp'] = hp
    return row


class F1QualityTests(unittest.TestCase):
    def proposals(self, rows, *, prefer=True):
        return F1WinnerReuse(True, prefer_hp=prefer).observe(source_result(rows), 'source', TEMPLATE)

    @staticmethod
    def indices(specs):
        return [spec['repair']['member_index'] for spec in specs]

    def test_default_keeps_member_order_even_when_later_hp_is_higher(self):
        rows = [winner(3, 60), winner(1, 9), winner(2, 40)]
        implicit = F1WinnerReuse(True).observe(source_result(rows), 'source', TEMPLATE)
        explicit = self.proposals(rows, prefer=False)
        self.assertEqual(self.indices(implicit), [1, 2, 3])
        self.assertEqual(canonical(implicit), canonical(explicit))

    def test_on_sorts_valid_projected_hp_descending(self):
        self.assertEqual(self.indices(self.proposals([winner(1, 9), winner(2, 50), winner(3, 20)])), [2, 3, 1])

    def test_hp_tie_is_member_then_original_source_order(self):
        rows = [winner(4, 50, plan='A'), winner(2, 50, plan='B'), winner(2, 50, plan='C')]
        specs = self.proposals(rows)
        self.assertEqual(self.indices(specs), [2, 2, 4])
        self.assertEqual([spec['request']['f1_winner_proposal']['route_actions'][0]['CardId'] for spec in specs], ['B', 'C', 'A'])

    def test_missing_hp_is_retained_last_without_parsing_audit_json(self):
        unknown = winner(1)
        unknown['forecast_native_json'] = '{"hp":100000}'
        specs = self.proposals([unknown, winner(2, 12), winner(3)])
        self.assertEqual(self.indices(specs), [2, 1, 3])
        self.assertNotIn('hp', specs[1]['request']['f1_winner_proposal']['forecast'])

    def test_nonpositive_boolean_string_and_null_hp_are_retained_as_unknown(self):
        rows = [winner(1, 0), winner(2, -7), winner(3, True), winner(4, '90'), winner(5), winner(6, 1)]
        rows[4]['forecast']['hp'] = None
        specs = self.proposals(rows)
        self.assertEqual(self.indices(specs), [6, 1, 2, 3, 4, 5])
        canonical(specs)

    def test_selected_plan_is_marked_before_high_hp_duplicate(self):
        selected = winner(4, 8, selected=True, plan='SAME')
        duplicate = winner(1, 99, plan='SAME')
        different = winner(2, 21)
        manager = F1WinnerReuse(True, prefer_hp=True)
        specs = manager.observe(source_result([duplicate, different, selected]), 'source', TEMPLATE)
        self.assertEqual(self.indices(specs), [2])
        self.assertEqual(manager.snapshot()['duplicate_plans'], 1)
        self.assertEqual(manager.snapshot()['selected_full_plans_marked_used'], 1)

    def test_hp_changes_do_not_create_new_plan_identity(self):
        manager = F1WinnerReuse(True, prefer_hp=True)
        self.assertEqual(len(manager.observe(source_result([winner(1, 10)]), 'first', TEMPLATE)), 1)
        self.assertEqual(manager.observe(source_result([winner(1, 90)]), 'second', TEMPLATE), [])

    def test_ordering_never_changes_stored_plan_or_actual_source_policy(self):
        rows = [winner(1, 8), winner(2, 40)]
        result = source_result(rows)
        source = deepcopy(TEMPLATE)
        source.update(policy_seed=91, policy_prior={'card:C': [2, 0, -1]})
        source['advisor']['prefer_f1_hp'] = True
        before = canonical(result)
        specs = F1WinnerReuse(True, prefer_hp=True).observe(result, 'source', TEMPLATE, source_request=source)
        self.assertEqual(canonical(result), before)
        for spec in specs:
            original = next(row for row in rows if row['member_index'] == spec['repair']['member_index'])
            self.assertEqual(spec['request']['f1_winner_proposal'], original)
            self.assertEqual(spec['request']['policy_seed'], 91)
            self.assertEqual(spec['request']['policy_prior'], source['policy_prior'])
            self.assertEqual(spec['request']['advisor'], source['advisor'])

    def test_prediction_does_not_bypass_forecast_native_win_or_root_guards(self):
        for change in ('loss', 'unobserved', 'context', 'progress', 'synthetic', 'unknown'):
            result = source_result([winner(1, 99)])
            row = result['f1_winner_candidates'][0]
            if change == 'loss': row['forecast']['won'] = False
            elif change == 'unobserved': result['f1_winner_reuse']['native_boss_wins'] = []
            elif change == 'context': row['context']['seed'] = 'different'
            elif change == 'progress': row.pop('native_progress')
            elif change == 'synthetic': result['synthetic'] = True
            elif change == 'unknown': result['status'] = 'UNKNOWN'
            with self.subTest(change=change):
                self.assertEqual(F1WinnerReuse(True, prefer_hp=True).observe(result, 'source', TEMPLATE), [])

    def test_preference_alone_does_not_enable_reuse(self):
        manager = F1WinnerReuse(prefer_hp=True)
        self.assertEqual(manager.observe(source_result([winner(1, 40)]), 'source', TEMPLATE), [])
        self.assertTrue(manager.snapshot()['prefer_predicted_f1_hp'])
        self.assertEqual(manager.snapshot()['alternative_requests'], 0)


if __name__ == '__main__':
    unittest.main()
