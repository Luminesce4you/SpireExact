import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from spire_exact.canonical import ContractError
from spire_exact.mode1 import (OBJECTIVE, CandidateBudget, check_winning_replay, context,
                              is_winning_candidate, room_summaries, solve_mode1)


def winning():
    return {'status': 'TERMINAL', 'native_terminal_observed': True, 'objective': OBJECTIVE['id'],
            'reason': None, 'value': [1, 500], 'trace': [{'kind': 'test_action'}], 'consumed': 1,
            'decision_evidence': [{'phase': 'test', 'observation': {'floor': 1}}],
            'observation': {'floor': 50, 'hp': '10'}, 'complete': False}


class Mode1Tests(unittest.TestCase):
    def setUp(self):
        self.ctx = context('42', 'IRONCLAD', 0, 'all')
        self.identity = {'context': self.ctx, 'native': {'game_sha256': 'a'*64, 'host_sha256': 'b'*64}}

    def test_win_meets_boolean_upper_bound_without_exhaustive_actions(self):
        proof = check_winning_replay(winning(), winning(), self.identity, self.identity)
        self.assertEqual((proof['lower_bound'], proof['upper_bound']), (1, 1))
        self.assertTrue(proof['optimal_in_backend'])
        self.assertFalse(proof['game_equivalence_verified'])
        self.assertFalse(proof['proves_highest_score'])

    def test_high_score_without_victory_is_not_witness(self):
        d = winning(); d['value'] = [0, 10**100]
        self.assertFalse(is_winning_candidate(d))

    def test_partial_or_unobserved_win_rejected(self):
        for field, value in (('status', 'DECISION'), ('native_terminal_observed', False),
                             ('reason', 'timeout'), ('value', [True]), ('objective', 'score')):
            d = winning(); d[field] = value
            with self.assertRaises(ContractError):
                check_winning_replay(d, winning(), self.identity, self.identity)

    def test_replay_divergence_and_truncation_rejected(self):
        for field, value in (('trace', []), ('consumed', 0), ('decision_evidence', []),
                             ('observation', {'hp': '11'})):
            d = winning(); d[field] = value
            with self.assertRaises(ContractError):
                check_winning_replay(winning(), d, self.identity, self.identity)

    def test_missing_evidence_on_both_sides_is_not_proof(self):
        d = winning(); d.pop('decision_evidence')
        with self.assertRaises(ContractError):
            check_winning_replay(d, d, self.identity, self.identity)

    def test_changed_seed_or_binary_invalidates(self):
        for change in ('seed', 'binary', 'objective'):
            identity = copy.deepcopy(self.identity)
            if change == 'seed': identity['context']['seed'] = '43'
            elif change == 'objective': identity['context']['objective']['id'] = 'score'
            else: identity['native']['game_sha256'] = 'c'*64
            with self.assertRaises(ContractError):
                check_winning_replay(winning(), winning(), self.identity, identity)

    def test_candidate_failure_never_prunes_region_or_proves_no_win(self):
        identity = self.identity
        class Backend:
            def execute(self, *args, **kwargs):
                d = winning(); d['value'] = [0, 999]
                return d, identity
        with tempfile.TemporaryDirectory() as tmp:
            result = solve_mode1(self.ctx, Path(tmp)/'result', CandidateBudget(attempts=3), backend=Backend())
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual((result['lower_bound'], result['upper_bound']), (0, 1))
        self.assertIsNone(result['best_verified_value'])
        self.assertEqual(result['pruned_regions'], [])
        self.assertTrue(result['unresolved_regions'])

    def test_fresh_replay_is_required_and_disables_generation(self):
        identity = self.identity
        calls = []
        class Backend:
            def execute(self, *args, **kwargs):
                calls.append(kwargs)
                return winning(), identity
        with tempfile.TemporaryDirectory() as tmp:
            result = solve_mode1(self.ctx, Path(tmp)/'result', backend=Backend())
        self.assertEqual(len(calls), 2)
        self.assertNotIn('history', calls[0])
        self.assertEqual(calls[1]['history'], winning()['trace'])
        self.assertTrue(result['optimal_in_backend'])

    def test_failed_verification_preserves_unknown(self):
        identity = self.identity
        class Backend:
            def execute(self, *args, **kwargs):
                d = winning()
                if 'history' in kwargs: d['value'] = [0, 0]
                return d, identity
        with tempfile.TemporaryDirectory() as tmp:
            result = solve_mode1(self.ctx, Path(tmp)/'result', CandidateBudget(attempts=1), backend=Backend())
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual(result['upper_bound'], 1)

    def test_unsupported_backend_is_unknown(self):
        class Backend:
            def execute(self, *args, **kwargs): raise ContractError('unsupported native choice')
        with tempfile.TemporaryDirectory() as tmp:
            result = solve_mode1(self.ctx, Path(tmp)/'result', CandidateBudget(attempts=1), backend=Backend())
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertIsNone(result['best_verified_value'])

    def test_score_objective_cannot_be_switched_into_mode1(self):
        self.ctx['objective']['secondary_objective'] = 'score'
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ContractError):
            solve_mode1(self.ctx, Path(tmp)/'result')

    def test_room_summaries_do_not_promote_unverified_or_pareto_merge(self):
        d = winning()
        d['trace'] *= 2
        d['decision_evidence'] = [{'observation': {'floor': 1}}, {'observation': {'floor': 2}}]
        summary = room_summaries(self.ctx, d)[0]
        self.assertEqual(summary['verified_transitions'], [])
        self.assertEqual(summary['possible_results']['victory_upper_bound'], 1)
        self.assertEqual(summary['exclusion_certificates'], [])

    def test_time_budget_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp, patch('spire_exact.mode1.perf_counter', side_effect=[0, 2]):
            result = solve_mode1(self.ctx, Path(tmp)/'result', CandidateBudget(seconds=1), backend=object())
        self.assertEqual(result['stop_reason'], 'time_budget')
        self.assertFalse(result['optimal_in_backend'])
