import unittest
from tools.audit_boss_progress import update_curve, observed_boss_passages


class BossProgressTests(unittest.TestCase):
    def test_new_life_progress_is_not_erased_by_fraction_reset(self):
        curves={}
        for revivals,fraction in [(0,.99),(1,.1)]:
            update_curve(curves,'boss',{'label':str(revivals)},
                {'available':True,'revivals_observed':revivals,'hp_removed_fraction':fraction,
                 'current_life_hp_removed_fraction':fraction,'turns_observed':4},1)
        self.assertEqual(len(curves['boss']),2)
        self.assertGreater(curves['boss'][1]['progress_signature'],curves['boss'][0]['progress_signature'])
    def test_next_room_alive_records_passage_but_same_room_does_not(self):
        entry = {'act': 0, 'floor': 17, 'room': 'Boss', 'hp': '50', 'enemies': [{'id': 'BOSS'}]}
        result = {'decision_evidence': [{'observation': entry}],
                  'observation': {'act': 0, 'floor': 17, 'hp': '40'}}
        self.assertEqual(observed_boss_passages(result), [])
        result['observation'] = {'act': 1, 'floor': 18, 'hp': '60'}
        self.assertEqual(observed_boss_passages(result), [{'act': 0, 'floor': 17, 'enemies': ['BOSS']}])

    def test_regression_does_not_lower_best_or_create_a_win(self):
        curves = {}
        for i, value in enumerate([.2, .5, .3, .5, .8]):
            update_curve(curves, 'boss', {'label': str(i), 'completed_wall_seconds': i},
                         {'available': True, 'hp_removed_fraction': value, 'turns_observed': 3}, 10*i)
        self.assertEqual([p['hp_removed_fraction'] for p in curves['boss']], [.2, .5, .8])
        self.assertTrue(all(p['win'] is False for p in curves['boss']))
        self.assertEqual(curves['boss'][-1]['cumulative_expanded_nodes'], 40)

    def test_missing_progress_is_not_zero_or_success(self):
        curves = {}
        update_curve(curves, 'boss', {'label': 'missing'}, {'available': False}, 0)
        self.assertEqual(curves, {})

    def test_distinct_bosses_and_longer_survival_remain_visible(self):
        curves = {}
        for key, turns in [('first', 3), ('second', 2), ('first', 4)]:
            update_curve(curves, key, {'label': key},
                         {'available': True, 'hp_removed_fraction': .5, 'turns_observed': turns}, 1)
        self.assertEqual(len(curves['first']), 2)
        self.assertEqual(len(curves['second']), 1)
