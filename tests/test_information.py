import copy
from pathlib import Path
import random
import unittest
from fractions import Fraction
from unittest.mock import patch
from spire_exact.canonical import ContractError, read_json
from spire_exact.core import Limits
from spire_exact.information import solve_information

ROOT = Path(__file__).resolve().parents[1]


class InformationTests(unittest.TestCase):
    def setUp(self):
        self.spec = read_json(ROOT / 'examples/information_trap.json')

    def test_hidden_information_cannot_choose_lucky_world(self):
        result = solve_information(self.spec)
        self.assertEqual(result['win_probability'], [1, 1])
        self.assertEqual(result['expected_score'], [80, 1])
        self.assertEqual(result['policy'][0]['action'], {'id': 'inspect'})
        choices = {p['observation']: p['action']['id'] for p in result['policy']}
        self.assertEqual(choices['left revealed'], 'left')
        self.assertEqual(choices['right revealed'], 'right')
        self.assertFalse(result['game_equivalence_verified'])

    def test_omniscient_can_use_selected_world(self):
        for world in ('left', 'right'):
            result = solve_information(self.spec, 'omniscient', world)
            self.assertEqual(result['expected_score'], [100, 1])
            self.assertEqual(result['policy'][0]['action'], {'id': 'guess-' + world})

    def test_no_information_value_without_revelation(self):
        for w in self.spec['worlds']:
            w['states']['seen']['observation'] = 'still unknown'
        result = solve_information(self.spec)
        self.assertEqual(result['win_probability'], [1, 2])
        self.assertEqual(result['expected_score'], [50, 1])

    def test_prior_controls_probability_not_known_seed(self):
        for w in self.spec['worlds']:
            w['states']['r']['actions'].pop()
        self.spec['worlds'][0]['probability'] = [1, 3]
        self.spec['worlds'][1]['probability'] = [2, 3]
        result = solve_information(self.spec)
        self.assertEqual(result['win_probability'], [2, 3])
        self.assertEqual(result['expected_score'], [200, 3])
        self.assertEqual(result['policy'][0]['action']['id'], 'guess-right')

    def test_victory_has_priority_over_arbitrarily_large_score(self):
        self.spec['worlds'][0]['states']['win']['terminal']['score'] = 10**40
        result = solve_information(self.spec)
        self.assertEqual(result['policy'][0]['action']['id'], 'inspect')

    def test_limits_never_certify(self):
        for limits in (Limits(max_states=1), Limits(max_edges=1), Limits(max_expansions=1)):
            result = solve_information(self.spec, limits=limits)
            self.assertFalse(result['proven_optimal_in_model'])
            self.assertIsNone(result['win_probability'])
            self.assertEqual(result['policy'], [])

    def test_time_budget(self):
        with patch('spire_exact.information.perf_counter', side_effect=[0, 2]):
            result = solve_information(self.spec, limits=Limits(max_seconds=1))
        self.assertEqual(result['stop_reason'], 'time_budget')

    def test_cycle_is_not_loss_or_optimal(self):
        for w in self.spec['worlds']:
            w['states']['r']['actions'].append({'action': {'id': 'loop'}, 'to': 'r'})
        result = solve_information(self.spec)
        self.assertEqual(result['status'], 'INCOMPLETE')

    def test_unknown_actions_block_proof(self):
        self.spec['worlds'][0]['states']['seen']['complete'] = False
        result = solve_information(self.spec)
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertTrue(result['unsupported'])

    def test_inconsistent_visible_menus_rejected(self):
        self.spec['worlds'][0]['states']['r']['actions'].pop()
        with self.assertRaises(ContractError):
            solve_information(self.spec)

    def test_bad_priors_and_missing_world_rejected(self):
        for value in ([1, 0], [2, 3], 0, -1, 0.5):
            spec = copy.deepcopy(self.spec)
            spec['worlds'][0]['probability'] = value
            with self.assertRaises((ContractError, TypeError)):
                solve_information(spec)
        with self.assertRaises(ContractError):
            solve_information(self.spec, 'omniscient')

    def test_random_one_step_policies_against_independent_oracle(self):
        rng = random.Random(90)
        for _ in range(50):
            worlds = []
            outcomes = [[(bool(rng.randrange(2)), rng.randrange(200)) for _ in range(4)] for _ in range(3)]
            for i, row in enumerate(outcomes):
                states = {'r': {'observation': 'same', 'actions': []}}
                for a, (won, score) in enumerate(row):
                    states['r']['actions'].append({'action': {'id': str(a)}, 'to': str(a)})
                    states[str(a)] = {'observation': [won, score], 'terminal': {'victory': won, 'score': score}}
                worlds.append({'id': str(i), 'probability': [1, 3], 'root': 'r', 'states': states})
            best = max((sum(Fraction(int(row[a][0]), 3) for row in outcomes),
                        sum(Fraction(row[a][1], 3) for row in outcomes)) for a in range(4))
            result = solve_information({'schema': 'spire-information-model/v1', 'worlds': worlds})
            self.assertEqual(Fraction(*result['win_probability']), best[0])
            self.assertEqual(Fraction(*result['expected_score']), best[1])
