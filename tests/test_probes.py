"""Synthetic gate probes: request construction, outcome reading, and isolation from real results."""
import unittest
from collections import Counter
from pathlib import Path
from spire_exact.canonical import canonical
from spire_exact.mode1 import is_winning_candidate
from spire_exact.planning.archive import classify_failure, utility
from spire_exact.planning.pool import NativePool, WorkerError
from spire_exact.planning.probes import (gate_entries, probe_request, probe_outcome, scaled, edit_candidates,
                                         offered_cards, PROBE_DECISIONS)


def card(name, upgrade=0): return {'id': name, 'upgrade': upgrade}


def enemy(hp, combat_id=1): return {'id': 'BOSS', 'combat_id': combat_id, 'hp': str(hp), 'block': '0'}


def trajectory(extra_before_fight=False):
    """A card decision, the move into the boss room, then a lost boss fight (100 -> 40)."""
    deck = [card('STRIKE'), card('STRIKE', 1), card('DEFEND'), card('BASH')]
    quiet = {'act': 1, 'floor': 32, 'hp': '50', 'max_hp': '80', 'deck': deck, 'relics': [], 'potions': [None]}
    options = [{'kind': 'card_reward', 'index': 0, 'card': 'INFLAME'}, {'kind': 'card_reward', 'index': 1, 'card': 'CLASH'}, {'kind': 'card_skip'}]
    trace = [options[2], {'kind': 'map', 'col': 3, 'row': 15}]
    evidence = [{'phase': 'card_reward', 'observation': quiet, 'available_actions': options, 'option_labels': [['card:INFLAME'], ['card:CLASH'], ['card_skip']]},
                {'phase': 'map', 'observation': quiet, 'available_actions': [trace[1]], 'option_labels': [['go:Boss@a1']]}]
    if extra_before_fight:
        trace.append({'kind': 'discard_potion', 'slot': 0}); evidence.append({'phase': 'map', 'observation': quiet, 'available_actions': [trace[-1]]})
    for turn, hp in ((1, 100), (2, 70)):
        fight = dict(quiet, floor=33, room='Boss', turn=turn, enemies=[enemy(hp)])
        trace.append({'kind': 'end_turn'}); evidence.append({'phase': 'combat', 'observation': fight, 'available_actions': [{'kind': 'end_turn'}]})
    return {'status': 'TERMINAL', 'value': [0], 'reason': None, 'trace': trace, 'decision_evidence': evidence,
            'observation': dict(quiet, floor=33, room='Boss', hp='0'),
            'terminal_combat': {'act': 1, 'floor': 33, 'turn': 3, 'enemies': [dict(enemy(40), max_hp='100')]}}


REQUEST = {'seed': '42', 'character': 'IRONCLAD', 'ascension': 10, 'unlocks': 'all', 'history': [], 'generate_candidate': True,
           'policy_seed': 7, 'max_decisions': 12000, 'capture_checkpoints': True, 'low_io': True, 'event_driven_settle': True,
           'stop_at_floor': 99, 'policy_prior': {'card:X': [1]}, 'checkpoint': 'somewhere/map-000010.json.gz',
           'advisor': {'nodes': 60000, 'budget_ms': 600000, 'gate_plans': {'Boss': {'members': [{'mode': 'Evaluate', 'beam': 45, 'nodes': 120000}]}}}}


def probe_result(won, hp='31', entry_index=1):
    """What the host returns for a finished probe of `trajectory()` (fight rows after the entering action)."""
    base = trajectory()
    rows = base['decision_evidence']
    result = {'schema': 'spire-native-probe/v1', 'status': 'PROBE', 'synthetic': True, 'reason': None, 'value': None,
              'native_terminal_observed': False, 'trace': base['trace'], 'decision_evidence': rows,
              'probe': {'edits': [{'op': 'add', 'card': 'INFLAME', 'upgrade': 0}], 'entered': True, 'fought': True, 'won': won, 'entry_index': entry_index},
              'advisor_metrics': {'searches': [{'expanded_nodes': 1200, 'wall_us': 2500000}, {'expanded_nodes': 300, 'wall_us': 500000}]}}
    if won:
        result['observation'] = {'act': 1, 'floor': 33, 'room': 'Boss', 'hp': hp, 'max_hp': '86', 'turn': None, 'enemies': None}
        result['terminal_combat'] = None
    else:
        result['observation'] = base['observation']; result['terminal_combat'] = base['terminal_combat']
    return result


class GateEntryTests(unittest.TestCase):
    def test_entry_names_the_move_into_the_fight(self):
        entries = gate_entries(trajectory())
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual((entry['gate'], entry['enter'], entry['index']), ((1, 0), 1, 2))
        self.assertEqual(entry['lost'], (60.0, 100.0))
    def test_fight_with_a_decision_between_move_and_combat_is_left_out(self):
        self.assertEqual(gate_entries(trajectory(extra_before_fight=True)), [])


class ProbeRequestTests(unittest.TestCase):
    def setUp(self):
        self.base = trajectory(); self.entry = gate_entries(self.base)[0]
    def test_request_replays_to_the_map_decision_and_enters_by_the_real_move(self):
        edits = [{'op': 'add', 'card': 'INFLAME', 'upgrade': 0}]
        request = probe_request(REQUEST, self.base['trace'], self.entry, edits)
        self.assertEqual(request['history'], self.base['trace'][:1])
        self.assertEqual(request['probe'], {'edits': edits, 'enter': {'kind': 'map', 'col': 3, 'row': 15}})
        self.assertEqual(request['max_decisions'], 1 + PROBE_DECISIONS)
        self.assertTrue(request['generate_candidate']); self.assertTrue(request['event_driven_settle']); self.assertTrue(request['low_io'])
        canonical(request)                                       # plain JSON, no floats
    def test_request_never_carries_real_search_state(self):
        request = probe_request(REQUEST, self.base['trace'], self.entry)
        self.assertFalse(request['capture_checkpoints'])
        for key in ('checkpoint', 'stop_at_floor', 'policy_prior', 'stop_at_strategic_decision', 'expected_evidence'):
            self.assertNotIn(key, request)
        self.assertEqual(request['policy_seed'], 7)                  # the run's own rollout policy seed
    def test_options_and_advisor_patch(self):
        patch = {'gate_plans': {'Boss': {'members': [{'mode': 'Evaluate', 'beam': 45, 'nodes': 60000}]}}}
        request = probe_request(REQUEST, self.base['trace'], self.entry, hp=44, rng=3, advisor_patch=patch)
        self.assertEqual((request['probe']['hp'], request['probe']['rng']), (44, 3))
        self.assertEqual(request['advisor']['gate_plans'], patch['gate_plans']); self.assertEqual(request['advisor']['nodes'], 60000)
        self.assertEqual(REQUEST['advisor']['gate_plans']['Boss']['members'][0]['nodes'], 120000)     # the source request is untouched
    def test_request_does_not_alias_the_trajectory(self):
        request = probe_request(REQUEST, self.base['trace'], self.entry)
        request['history'][0]['kind'] = 'changed'; request['probe']['enter']['col'] = 9
        self.assertEqual(self.base['trace'][0]['kind'], 'card_skip'); self.assertEqual(self.base['trace'][1]['col'], 3)


class ProbeOutcomeTests(unittest.TestCase):
    def test_survived_fight_uses_the_entry_maximum_hp(self):
        outcome = probe_outcome(probe_result(True))
        self.assertTrue(outcome['won']); self.assertEqual((outcome['hp'], outcome['max_hp']), (31.0, 80.0))
        self.assertAlmostEqual(outcome['outcome'], 1 + 31 / 80); self.assertIsNone(outcome['lost'])
        self.assertEqual((outcome['nodes'], outcome['search_seconds']), (1500, 3.0))
        self.assertEqual(scaled(outcome, 100.0), outcome['outcome'])
    def test_lost_fight_reports_removed_hp_for_the_common_scale(self):
        outcome = probe_outcome(probe_result(False))
        self.assertFalse(outcome['won']); self.assertEqual(outcome['lost'], (60.0, 100.0))
        self.assertAlmostEqual(scaled(outcome, 200.0), 0.3); self.assertEqual(scaled(outcome, 0.0), 0.0)
    def test_unfinished_or_non_probe_results_have_no_outcome(self):
        failed = probe_result(True); failed.update(status='UNSUPPORTED', reason='PROBE_NOT_AT_MAP_BOUNDARY:event')
        self.assertIsNone(probe_outcome(failed))
        unfought = probe_result(True); unfought['probe'].update(fought=False, won=None)
        self.assertIsNone(probe_outcome(unfought))
        self.assertIsNone(probe_outcome(trajectory()))                       # a real result is never read as a probe
        unmarked = probe_result(True); unmarked['synthetic'] = False
        self.assertIsNone(probe_outcome(unmarked))
    def test_a_probe_result_is_never_a_win_a_death_or_a_viable_trajectory(self):
        for won in (True, False):
            result = probe_result(won)
            self.assertEqual(classify_failure(result), 'UNKNOWN')
            self.assertFalse(is_winning_candidate(result))
            self.assertEqual(utility(result)[:2], (0, 0))
        from spire_exact.planning.focus import FocusScheduler
        scheduler = FocusScheduler(); scheduler.add(probe_result(False), 'probe')
        self.assertEqual(scheduler.snapshot()['elite_pool'], 0)


class EditCandidateTests(unittest.TestCase):
    def test_candidates_cover_additions_removals_and_upgrades(self):
        entry = {'deck': [card('STRIKE'), card('STRIKE', 1), card('DEFEND', 1), card('BASH')]}
        rows = edit_candidates(entry, Counter({'INFLAME': 5, 'CLASH': 5, 'RARE': 1}), limit=2)
        self.assertEqual([label for label, _ in rows], ['card:CLASH', 'card:INFLAME', 'remove:BASH', 'remove:DEFEND', 'remove:STRIKE',
                                                         'upgrade:BASH', 'upgrade:STRIKE'])
        edits = dict(rows)
        self.assertEqual(edits['card:CLASH'], [{'op': 'add', 'card': 'CLASH', 'upgrade': 0}])
        self.assertEqual(edits['remove:STRIKE'], [{'op': 'remove', 'card': 'STRIKE', 'upgrade': 0}])       # the plain copy goes first
        self.assertEqual(edits['remove:DEFEND'], [{'op': 'remove', 'card': 'DEFEND', 'upgrade': 1}])
        self.assertNotIn('upgrade:DEFEND', edits)                                                           # no plain copy left to upgrade
    def test_offered_cards_are_counted_from_decision_labels(self):
        base = trajectory()
        self.assertEqual(offered_cards(base), Counter({'INFLAME': 1, 'CLASH': 1}))
        self.assertEqual(offered_cards(base, 0), Counter())


class DisposableWorkerTests(unittest.TestCase):
    def test_pool_refuses_a_probe_in_a_shared_worker(self):
        pool = object.__new__(NativePool)
        with self.assertRaises(WorkerError) as raised:
            NativePool.run(pool, {'probe': {'enter': {'kind': 'map'}}, 'history': []}, Path('unused'))
        self.assertIn('PROBE_REQUIRES_DISPOSABLE_WORKER', str(raised.exception))
    def test_probe_batch_accepts_only_probe_requests(self):
        pool = object.__new__(NativePool)
        for requests in ([], [{'history': []}], [{'probe': {}, 'history': []}]):
            with self.assertRaises(WorkerError):
                NativePool.run_probes(pool, requests, Path('unused'))


if __name__ == '__main__':
    unittest.main()
