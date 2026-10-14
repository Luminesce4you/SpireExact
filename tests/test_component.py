"""Component mode (PLAN-v2 R3): the whole-run search started at a door and stopped behind one gate (no native process)."""
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from spire_exact.canonical import ContractError, canonical
from spire_exact.planning import component as comp
from spire_exact.planning.gates import preset
from spire_exact.planning.search import SearchConfig, solve
from tests.test_focus_search import CTX, FakeEvaluator, world


class ScopedEvaluator(FakeEvaluator):
    def __init__(self, pool, ctx, directory, config, advisor, *, scope_prefix=None, stop_floor=None):
        super().__init__(pool, ctx, directory, config, advisor)
        self.scope_prefix, self.stop_floor = copy.deepcopy(scope_prefix), stop_floor


def pick(card):
    return {'kind': 'card_reward', 'index': ('BAD', 'GOOD', 'MEH').index(card), 'card': card}


def door(*cards):
    """The toy world's trace up to the given picks (all three picks = the boss door)."""
    return world([pick(c) for c in cards])['trace'][:len(cards)]


def run(component=None, evaluations=40, **overrides):
    FakeEvaluator.requests = []
    FakeEvaluator.policy_aware = FakeEvaluator.map_step = False
    options = dict(evaluations=evaluations, scheduler='focus', dispatch_mode='ordered', dispatch_window=4, repair_mode='gate',
                   prior=True, lookahead_floors=99, lookahead_actions=12000, max_decisions=12000,
                   gate_retry_plans=tuple(preset('escalate')['retries']))
    options.update(overrides)
    advisor = {'budget_ms': 1000, 'boss_budget_ms': 1000, 'nodes': 60000, 'gate_plans': preset('escalate')['plans']}
    with tempfile.TemporaryDirectory() as d, patch('spire_exact.planning.search.Evaluator', ScopedEvaluator):
        pool = SimpleNamespace(stats={}, resources=SimpleNamespace(as_dict=lambda: {}, workers=2))
        result = solve(CTX, Path(d), pool, SearchConfig(**options), advisor=advisor, component=component)
        json.dumps(result)
        written = json.loads((Path(d) / 'result.json').read_text(encoding='utf-8'))
        return result, list(FakeEvaluator.requests), written


class ComponentSearchTests(unittest.TestCase):
    def assert_scoped(self, requests, prefix, stop_floor):
        for kind, request in requests:
            self.assertEqual(canonical(request['history'][:len(prefix)]), canonical(prefix), kind)
            self.assertEqual(request.get('stop_at_floor'), stop_floor, kind)

    def test_a_passable_door_stops_at_the_first_result(self):
        prefix = door('GOOD', 'GOOD', 'BAD')
        result, requests, written = run(comp.Component(prefix, 'A1', stop_floor=4))
        self.assertEqual(result['stop_reason'], 'component_gate_passed')
        self.assertEqual(result['component']['passed']['label'], 'eval-0000-baseline')
        self.assertEqual(written['component'], result['component'])
        self.assertEqual((result['status'], result['lower_bound'], result['upper_bound']), ('UNKNOWN', 0, 1))
        self.assertFalse(result['diagnostic_scope']['counts_as_fresh_planner_run'])
        self.assertEqual([k for k, _ in requests], ['baseline'])
        self.assert_scoped(requests, prefix, 4)

    def test_the_search_finds_the_picks_after_the_door_and_never_changes_the_door(self):
        prefix = door('BAD')
        result, requests, _ = run(comp.Component(prefix, 'A1', stop_floor=4))
        self.assertEqual(result['stop_reason'], 'component_gate_passed')
        self.assertGreater(len(requests), 1)
        self.assert_scoped(requests, prefix, 4)
        self.assertIsNone(result['component']['exhausted'])
        passed = result['component']['passed']
        self.assertEqual(passed['scheduled_evaluations'], len(requests))
        self.assertLess(passed['evaluation_index'], len(requests))
        self.assertGreater(passed['evaluation_index'], 0)

    def test_a_lost_fight_door_stops_once_restarts_only_repeat(self):
        prefix = door('BAD', 'BAD', 'BAD')
        result, requests, _ = run(comp.Component(prefix, 'A1', stop_floor=4), evaluations=200)
        self.assertEqual(result['stop_reason'], 'component_exhausted')
        self.assertIsNone(result['component']['passed'])
        exhausted = result['component']['exhausted']
        self.assertEqual(exhausted['distinct_restart_trajectories'], 1)
        self.assertEqual(exhausted['repeated_restarts'], 4)          # 2 x the toy pool's 2 workers
        self.assertLess(len(requests), 200)
        self.assertEqual({k for k, _ in requests}, {'baseline', 'restart'})   # 20 % removed: no near miss, no retry
        self.assert_scoped(requests, prefix, 4)

    def test_queued_gate_retries_run_before_a_door_counts_as_exhausted(self):
        prefix = door('GOOD', 'BAD', 'BAD')                          # 50 % removed: a near miss
        result, requests, _ = run(comp.Component(prefix, 'A1', stop_floor=4), evaluations=200)
        self.assertEqual(result['stop_reason'], 'component_exhausted')
        kinds = [k for k, _ in requests]
        self.assertEqual(kinds.count('gate_retry'), len(preset('escalate')['retries']))
        self.assertLess(kinds.index('gate_retry'), len(kinds) - 4)
        self.assert_scoped(requests, prefix, 4)

    def test_a_lost_door_without_enough_repeats_runs_to_its_budget(self):
        prefix = door('BAD', 'BAD', 'BAD')
        result, requests, _ = run(comp.Component(prefix, 'A1', stop_floor=4), evaluations=4)
        self.assertEqual(result['stop_reason'], 'evaluation_budget')
        self.assertIsNone(result['component']['passed'])
        self.assertIsNone(result['component']['exhausted'])
        self.assertEqual(len(requests), 4)

    def test_another_gate_is_not_this_one(self):
        result, _, _ = run(comp.Component(door('GOOD', 'GOOD', 'BAD'), 'A2'), evaluations=6)
        self.assertIsNone(result['component']['passed'])

    def test_root_policies_start_at_the_door(self):
        prefix = door('BAD')
        result, requests, _ = run(comp.Component(prefix, 'A1', stop_floor=4), root_policies=('pick',))
        self.assertIn('root_policy', [k for k, _ in requests])
        self.assert_scoped(requests, prefix, 4)

    def test_refusals(self):
        prefix = door('BAD')
        with self.assertRaises(ContractError):
            run(comp.Component(prefix, 'A1'), scheduler='dfs')
        with tempfile.TemporaryDirectory() as d, patch('spire_exact.planning.search.Evaluator', ScopedEvaluator):
            pool = SimpleNamespace(stats={}, resources=SimpleNamespace(as_dict=lambda: {}, workers=2))
            with self.assertRaises(ContractError):
                solve(CTX, Path(d), pool, SearchConfig(scheduler='focus'), component=comp.Component(prefix, 'A1'),
                      initial_prefix=prefix)
        for bad in (dict(prefix=[], gate='A1'), dict(prefix=prefix, gate='B1'), dict(prefix=prefix, gate='A1', stop_floor=0)):
            with self.assertRaises(ContractError):
                comp.Component(bad['prefix'], bad['gate'], stop_floor=bad.get('stop_floor'))

    def test_f2_reroll_is_allowed_in_component_mode_only(self):
        from spire_exact.planning.plugins import reroll
        s = SimpleNamespace(pool=SimpleNamespace(stamp={'capabilities': [reroll.CAPABILITY]}), scope_prefix=[1],
                            ev=SimpleNamespace(advisor={'gate_plans': {'FinalBoss': {'members': [{}]}, 'Boss': {'members': [{}]}}}))
        with patch.object(reroll, 'last_act_boss_plan', return_value={'members': [{}]}):
            with self.assertRaises(ContractError):
                reroll.F2Reroll().attach(s)
            s.component = comp.Component([1], 'F1')
            reroll.F2Reroll().attach(s)


def entry(gate='F1', floor=48, **extra):
    record = {'schema': 'spire-gate-entry/v1', 'id': 'e1', 'group': 'g', 'run': 'r', 'label': 'eval-0001-x', 'gate': gate,
              'gate_tuple': list(comp.GATES[gate]), 'prefix': [{'kind': 'map'}], 'prefix_sha256': 'p',
              'entry': {'floor': floor}, 'solver_seed': 314159, 'expected': {'won': False},
              'recorded_identity': {'host_sha256': 'h'},
              'recorded_request': {'seed': '7', 'character': 'IRONCLAD', 'ascension': 10, 'unlocks': 'all', 'history': []}}
    record.update(extra)
    return record


class GateLibraryEntryTests(unittest.TestCase):
    def test_own_gate_stops_at_its_floor_a_later_gate_is_unbounded(self):
        own = comp.from_entry(entry())
        self.assertEqual((own.gate, own.stop_floor), ('F1', 48))
        self.assertEqual(own.run, {'seed': '7', 'character': 'IRONCLAD', 'ascension': 10, 'unlocks': 'all'})
        self.assertEqual(own.source['recorded_solver_seed'], 314159)
        later = comp.from_entry(entry(), 'F2')
        self.assertEqual((later.gate, later.stop_floor), ('F2', None))
        for record, gate in ((entry(), 'A2'), (entry(gate_tuple=[0, 0]), None), ({'schema': 'x'}, None)):
            with self.assertRaises(ContractError):
                comp.from_entry(record, gate)

    def test_load_entry_reads_the_library_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'lib.sqlite'
            db = sqlite3.connect(path)
            db.execute('CREATE TABLE entries (id TEXT PRIMARY KEY, record TEXT)')
            db.execute('INSERT INTO entries VALUES (?,?)', ('e1', json.dumps(entry())))
            db.commit(); db.close()
            self.assertEqual(comp.load_entry('e1', path)['gate'], 'F1')
            with self.assertRaises(ContractError):
                comp.load_entry('nope', path)

    def test_check_run(self):
        own = comp.from_entry(entry())
        comp.check_run(own, {'seed': '7', 'character': 'IRONCLAD', 'ascension': 10, 'unlocks': 'all'})
        with self.assertRaises(ContractError):
            comp.check_run(own, {'seed': '8', 'character': 'IRONCLAD', 'ascension': 10, 'unlocks': 'all'})

    @unittest.skipUnless(comp.LIBRARY.is_file(), 'no M1 gate library in this workspace')
    def test_every_real_library_entry_makes_a_component(self):
        db = sqlite3.connect(f'file:{comp.LIBRARY.as_posix()}?mode=ro', uri=True)
        rows = db.execute('SELECT id, gate, record FROM entries').fetchall()
        db.close()
        self.assertTrue(rows)
        for entry_id, gate, text in rows:
            component = comp.from_entry(json.loads(text))
            self.assertEqual(component.gate, gate)
            self.assertIsNotNone(component.stop_floor, entry_id)
            self.assertEqual(component.source['library_id'], entry_id)


class ComponentCliTests(unittest.TestCase):
    def test_options_are_classified_and_need_component(self):
        from spire_exact.planning.__main__ import build_parser
        from spire_exact.planning.registry import classified
        dests = {action.dest for action in build_parser()._actions if action.dest != 'help'}
        table = classified()
        for dest in ('component', 'component_gate', 'gate_library'):
            self.assertIn(dest, dests)
            self.assertEqual(table[dest], 'identity')


if __name__ == '__main__':
    unittest.main()
