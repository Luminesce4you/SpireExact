"""R1 (refactor/PLAN-v2.md): named configurations, the mechanism registry, the
allocator sources and the two plugins (P1 F2 reroll, P2 permuted predictor).
No native runtime: the full-panel replay (tools/replay_golden.py) checks that
the search without plugins is still frozen-i054a-shm."""
import json
import random
import tempfile
import unittest
from collections import Counter, deque
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from spire_exact.canonical import ContractError
from spire_exact.planning import policies, registry
from spire_exact.planning.__main__ import build_parser, expand_config
from spire_exact.planning.configs import CONFIG_DIR, config_argv, resolve
from spire_exact.planning.core import PriorityAllocator, Source, default_sources
from spire_exact.planning.gatemodel import GateModels, _Gate
from spire_exact.planning.plugins import Plugin, load, parse, register_tables
from spire_exact.planning.plugins.context import PluginContext
from spire_exact.planning.plugins import permuted as p2
from spire_exact.planning.plugins import reroll as p1
from spire_exact.planning.search import SearchConfig

ROOT = Path(__file__).resolve().parents[1]
CONFIG = CONFIG_DIR / 'i054a-shm.toml'
A_JSON = ROOT.parents[2] / 'P5plus-f1-card-switches' / 'experiments' / 'iteration-105' / 'configs' / 'A.json'

# iteration-105 arm A planner arguments without --seed (A.json planner_settings).
A_ARGV = ['--character', 'IRONCLAD', '--ascension', '10', '--unlocks', 'all', '--workers', '7', '--dop', '1',
          '--reserve-mib', '1024', '--evaluations', '1000000', '--seconds', '3570', '--task-seconds', '1200',
          '--max-decisions', '12000', '--lookahead-actions', '12000', '--lookahead-floors', '99',
          '--alternatives', '8', '--survivors', '2', '--budget-ms', '600000', '--boss-budget-ms', '600000',
          '--nodes', '60000', '--profile', 'Low', '--dispatch', 'ordered', '--dispatch-window', '56',
          '--solver-seed', '271828', '--low-io', '--event-driven-settle', '--checkpoint-mib', '1024',
          '--cache-mib', '128', '--archive-entries', '256', '--scheduler', 'focus', '--prior',
          '--gate-preset', 'escalate', '--repair-mode', 'gate', '--normal-nodes', '10000',
          '--runtime-profile', 'server-large-gen0', '--worker-memory-mib', '1792', '--root-policies', 'pick,elo',
          '--focus-cluster-cap', '2', '--final-gate-plan', 'open', '--worker-memory-policy', 'shared']
RUN = ['--seed', '1', '--out', 'unused-output']


def parsed(argv):
    return build_parser().parse_args(expand_config(list(argv)))


def write_toml(directory, text):
    path = Path(directory) / 'test.toml'
    path.write_text(text, encoding='utf-8')
    return path


class RegistryTests(unittest.TestCase):
    def test_every_option_is_classified_exactly_once(self):
        dests = {action.dest for action in build_parser()._actions if action.dest != 'help'}
        self.assertEqual(set(registry.classified()), dests)

    def test_every_search_config_field_has_an_option(self):
        dests = {action.dest for action in build_parser()._actions}
        missing = [f.name for f in fields(SearchConfig) if registry.CONFIG_FIELDS.get(f.name, f.name) not in dests]
        self.assertEqual(missing, [])

    def test_entries(self):
        for entry in registry.MECHANISMS.values():
            self.assertIn(entry.status, registry.STATUSES)
            self.assertTrue(callable(entry.active), entry.name)
            self.assertIsNone(entry.target)
        for entry in registry.PLUGINS.values():
            self.assertEqual(entry.status, registry.EXPERIMENTAL)
            self.assertEqual(entry.options, ())
            self.assertIn(':', entry.target)

    def test_plugin_targets_import_with_their_names(self):
        for name in registry.PLUGINS:
            (plugin,) = load([name])
            self.assertEqual(plugin.name, name)


class ConfigTests(unittest.TestCase):
    def test_default_config_is_iteration_105_arm_a(self):
        self.assertEqual(config_argv(CONFIG), A_ARGV)

    @unittest.skipUnless(A_JSON.is_file(), 'iteration-105 A.json not present')
    def test_default_config_matches_a_json(self):
        settings = list(json.loads(A_JSON.read_text(encoding='utf-8'))['planner_settings'])
        index = settings.index('--seed')
        del settings[index:index + 2]
        self.assertEqual(config_argv(CONFIG), settings)

    def test_resolve(self):
        self.assertEqual(resolve('i054a-shm'), CONFIG)
        self.assertEqual(resolve(str(CONFIG)), CONFIG)
        with self.assertRaises(ValueError):
            resolve('no-such-configuration')

    def test_named_config_parses_like_its_arguments(self):
        named = parsed(['--config', 'i054a-shm', *RUN])
        direct = build_parser().parse_args(A_ARGV + RUN)
        self.assertEqual({k: v for k, v in vars(named).items() if k != 'config'},
                         {k: v for k, v in vars(direct).items() if k != 'config'})

    def test_command_line_overrides_the_config(self):
        self.assertEqual(parsed(['--config', 'i054a-shm', '--solver-seed', '314159', *RUN]).solver_seed, 314159)
        self.assertEqual(parsed(['--solver-seed', '314159', '--config', 'i054a-shm', *RUN]).solver_seed, 314159)

    def test_default_config_activates_only_allowed_mechanisms(self):
        self.assertEqual(registry.not_allowed(parsed(['--config', 'i054a-shm', *RUN])), [])

    def test_a_plugin_is_not_allowed_by_default(self):
        args = parsed(['--config', 'i054a-shm', '--plugin', 'p2-permuted-predictor', *RUN])
        self.assertEqual(registry.not_allowed(args), [('p2-permuted-predictor', registry.EXPERIMENTAL)])
        self.assertEqual(registry.not_allowed(args, registry.DEFAULT_ALLOWED | {registry.EXPERIMENTAL}), [])

    def test_retired_mechanisms_are_reported(self):
        names = [name for name, _ in registry.not_allowed(parsed(['--config', 'i054a-shm', '--focus-family', *RUN]))]
        self.assertEqual(names, ['focus-family'])

    def test_config_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_toml(directory, 'name = "t"\n[planner]\nscheduler = "focus"\nprior = true\nlow-io = false\n'
                                         'workers = 3\n[plugins.p1-f2-reroll]\nk = 2\nshare = 10\n'
                                         '[plugins.p2-permuted-predictor]\n')
            self.assertEqual(config_argv(path), ['--scheduler', 'focus', '--prior', '--workers', '3',
                                                 '--plugin', 'p1-f2-reroll:k=2,share=10',
                                                 '--plugin', 'p2-permuted-predictor'])

    def test_config_refusals(self):
        bad = ['[planner]\nseed = 1\n', '[planner]\nout = "x"\n', '[planner]\nplugin = "x"\n',
               '[planner]\nnodes = 1.5\n', '[planner]\nroot-policies = ["pick"]\n', '[other]\nx = 1\n',
               '[plugins.p1-f2-reroll]\nk = "2"\n']
        with tempfile.TemporaryDirectory() as directory:
            for text in bad:
                with self.assertRaises(ValueError, msg=text):
                    config_argv(write_toml(directory, text))


class PluginLoadingTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse('p1-f2-reroll'), ('p1-f2-reroll', {}))
        self.assertEqual(parse('p1-f2-reroll:k=2, share=10'), ('p1-f2-reroll', {'k': 2, 'share': 10}))
        self.assertEqual(parse('x:a-b=1'), ('x', {'a_b': 1}))
        for text in ('', ':k=1', 'x:k', 'x:=1', 'x:k=1,k=2', 'x:k=a'):
            with self.assertRaises(ValueError, msg=text):
                parse(text)

    def test_load(self):
        a, b = load(['p2-permuted-predictor', 'p1-f2-reroll:k=2,share=10'])
        self.assertIsInstance(a, p2.PermutedPredictor)
        self.assertEqual(b.describe(), {'name': 'p1-f2-reroll', 'params': {'k': 2, 'share': 10}})
        self.assertEqual(load([]), [])
        for texts in (['no-such-plugin'], ['p1-f2-reroll', 'p1-f2-reroll:k=2'], ['p1-f2-reroll:k=9'],
                      ['p1-f2-reroll:share=101'], ['p2-permuted-predictor:x=1']):
            with self.assertRaises(ValueError, msg=texts):
                load(texts)

    def test_plugin_parameters(self):
        class Sample(Plugin):
            name = 'sample'
            PARAMS = {'b': 1, 'a': 2}
        self.assertEqual(Sample(b=5).params, {'b': 5, 'a': 2})
        self.assertEqual(list(Sample().describe()['params']), ['a', 'b'])
        for params in ({'c': 1}, {'a': '1'}, {'a': True}, {'a': 1.0}):
            with self.assertRaises(ValueError, msg=params):
                Sample(**params)

    def test_main_rejects_bad_plugins(self):
        from spire_exact.planning.__main__ import main
        with self.assertRaises(SystemExit), patch('sys.stderr'):
            main(['--plugin', 'no-such-plugin', '--out', 'unused-output'])


class PolicyRegistrationTests(unittest.TestCase):
    NAME = 'test-refactor-table'

    def tearDown(self):
        policies.POLICIES.pop(self.NAME, None)

    def test_register(self):
        policies.register(self.NAME, {'Strike': [1, 0], 'Bash': (2,)})
        self.assertEqual(policies.POLICIES[self.NAME], {'Strike': [1, 0], 'Bash': [2]})
        policies.register(self.NAME, {'Strike': [1, 0], 'Bash': [2]})          # same table: no-op
        self.assertEqual(policies.resolve(('pick', self.NAME)), ('pick', self.NAME))
        with self.assertRaises(ValueError):
            policies.register(self.NAME, {'Strike': [2]})

    def test_refusals(self):
        limit = policies.LIMIT
        for name, table in ((policies.NATIVE, {'Strike': [1]}), ('', {'Strike': [1]}), (None, {'Strike': [1]}),
                            (self.NAME, {}), (self.NAME, {'Strike': []}), (self.NAME, {'Strike': [limit + 1]}),
                            (self.NAME, {'Strike': [-limit - 1]}), (self.NAME, {'Strike': [True]}),
                            (self.NAME, {'Strike': [1.0]}), (self.NAME, {1: [1]}), (self.NAME, {'Strike': 1})):
            with self.assertRaises(ValueError, msg=(name, table)):
                policies.register(name, table)
        self.assertNotIn(self.NAME, policies.POLICIES)

    def test_register_tables(self):
        class Tables(Plugin):
            name = 'tables'
            def tables(self):
                return {PolicyRegistrationTests.NAME: {'Strike': [1]}}
        register_tables([Plugin(), Tables()])
        self.assertEqual(policies.POLICIES[self.NAME], {'Strike': [1]})


class FakeSearch:
    """Request sources of core.Search, with labelled specs."""
    KINDS = ('macro_focus', 'macro_explore', 'restart', 'gate_retry', 'combat_probe', 'root_policy', 'f2_reroll')

    def __init__(self, seed):
        rng = random.Random(seed)
        self.scheduled = Counter({kind: rng.randrange(30) for kind in self.KINDS if rng.random() < .7})
        self.urgent = deque({'kind': 'root_policy', 'id': f'u{i}'} for i in range(rng.randrange(3)))
        self.repairs = deque({'kind': rng.choice(('gate_retry', 'combat_probe')), 'id': f'r{i}'}
                             for i in range(rng.randrange(12)))
        self.macros = deque(None if rng.random() < .2 else {'kind': 'macro_focus', 'id': f'm{i}'}
                            for i in range(rng.randrange(12)))
        self.restarts = 0
        self.slots = [rng.randrange(10) for _ in range(5)]

    def macro_spec(self):
        return self.macros.popleft() if self.macros else None

    def restart_spec(self, slot):
        self.restarts += 1
        return {'kind': 'restart', 'id': f's{self.restarts}:{slot}'}


def frozen_fill(s, slots):
    """PriorityAllocator.fill of frozen-i054a-shm (refactor/backup-1/core.py)."""
    specs = []
    for slot in range(slots):
        if s.urgent:
            specs.append(s.urgent.popleft())
            continue
        repairs = (sum(s.scheduled[kind] for kind in ('combat_probe', 'gate_retry'))
                   + sum(spec['kind'] in ('combat_probe', 'gate_retry') for spec in specs))
        if s.repairs and (repairs == 0 or repairs < .2 * (sum(s.scheduled.values()) + len(specs) + 1)):
            specs.append(s.repairs.popleft())
            continue
        specs.append(s.macro_spec() or s.restart_spec(slot))
    return specs


class AllocatorTests(unittest.TestCase):
    def test_default_sources_fill_like_frozen_i054a(self):
        for seed in range(300):
            old, new = FakeSearch(seed), FakeSearch(seed)
            allocator = PriorityAllocator(default_sources(new))
            for slots in old.slots:
                expected, got = frozen_fill(old, slots), allocator.fill(new, slots)
                self.assertEqual([spec['id'] for spec in got], [spec['id'] for spec in expected], seed)
                old.scheduled.update(spec['kind'] for spec in expected)
                new.scheduled.update(spec['kind'] for spec in got)
            self.assertEqual((len(new.urgent), len(new.repairs), len(new.macros), new.restarts),
                             (len(old.urgent), len(old.repairs), len(old.macros), old.restarts))

    def plugin_source(self, queue, share):
        return Source('plugin', lambda slot: queue.popleft() if queue else None, pending=lambda: bool(queue),
                      share=share, kinds=('f2_reroll',))

    def test_plugin_sources_sit_between_repairs_and_scheduler(self):
        s = FakeSearch(0)
        s.scheduled = Counter()
        s.urgent = deque([{'kind': 'root_policy', 'id': 'u'}])
        s.repairs = deque([{'kind': 'gate_retry', 'id': 'r'}])
        s.macros = deque([{'kind': 'macro_focus', 'id': 'm'}])
        queue = deque([{'kind': 'f2_reroll', 'id': 'p'}])
        specs = PriorityAllocator(default_sources(s, [self.plugin_source(queue, None)])).fill(s, 5)
        self.assertEqual([spec['id'] for spec in specs], ['u', 'r', 'p', 'm', 's1:4'])

    def test_plugin_share(self):
        s = FakeSearch(0)
        s.scheduled = Counter({'macro_focus': 6})
        s.urgent, s.repairs = deque(), deque()
        s.macros = deque({'kind': 'macro_focus', 'id': f'm{i}'} for i in range(10))
        queue = deque({'kind': 'f2_reroll', 'id': f'p{i}'} for i in range(10))
        specs = PriorityAllocator(default_sources(s, [self.plugin_source(queue, .25)])).fill(s, 8)
        # used < .25 * (6 scheduled + chosen + 1): allowed at 0/7, 1/8, 2/9, 3/13; refused at 3/10..3/12, 4/14
        self.assertEqual([spec['kind'] for spec in specs],
                         ['f2_reroll'] * 3 + ['macro_focus'] * 3 + ['f2_reroll', 'macro_focus'])


class PermutedPredictorTests(unittest.TestCase):
    ROWS = [({'x': i}, i / 10) for i in range(10)]

    def test_base_targets_are_unchanged(self):
        self.assertIs(GateModels(minimum=8, refresh=4)._targets(_Gate(), self.ROWS, 'level'), self.ROWS)

    def test_permutation(self):
        model = p2.permuted(GateModels)(271828, minimum=8, refresh=4)
        self.assertEqual(type(model).__name__, 'PermutedGateModels')
        gate = model.gates[(3, 1)] = _Gate()
        first = model._targets(gate, self.ROWS, 'level')
        self.assertEqual(first, model._targets(gate, self.ROWS, 'level'))          # reproducible
        self.assertEqual([f for f, _ in first], [f for f, _ in self.ROWS])          # features stay in place
        self.assertEqual(sorted(t for _, t in first), sorted(t for _, t in self.ROWS))
        self.assertNotEqual(first, self.ROWS)
        self.assertEqual(model.permuted_fits, {'level': 2, 'pairs': 0})
        model.version += 1
        self.assertNotEqual(model._targets(gate, self.ROWS, 'level'), first)       # a fresh shuffle per fit
        other = p2.permuted(GateModels)(314159, minimum=8, refresh=4)
        other.gates[(3, 1)] = gate
        self.assertNotEqual(other._targets(gate, self.ROWS, 'level'), first)

    def test_fit_uses_the_hook(self):
        def gate():
            g = _Gate()
            for i in range(30):
                g.entries[f'e{i}'] = [{'x': i % 5, 'y': (i * 7) % 3}, 1.0 if i % 5 >= 3 else 0.0, None]
            return g
        base, null = GateModels(minimum=8, refresh=4), p2.permuted(GateModels)(minimum=8, refresh=4)
        real, shuffled = gate(), gate()
        base.gates[(1, 0)], null.gates[(1, 0)] = real, shuffled
        base._fit(real)
        null._fit(shuffled)
        self.assertEqual(null.permuted_fits['level'], 1)
        self.assertNotEqual(real.weights, shuffled.weights)
        self.assertEqual([row[1] for row in shuffled.entries.values()], [row[1] for row in real.entries.values()])

    def test_attach_refusals(self):
        plugin = p2.PermutedPredictor()
        good = SimpleNamespace(focus_family=False, focus_optimism=0, gate_probe_schedule=())
        plugin.attach(PluginContext(SimpleNamespace(models=object(), config=good)))
        for s in (SimpleNamespace(models=None, config=good),
                  SimpleNamespace(models=object(), config=SimpleNamespace(**{**vars(good), 'focus_family': True})),
                  SimpleNamespace(models=object(), config=SimpleNamespace(**{**vars(good), 'focus_optimism': 50})),
                  SimpleNamespace(models=object(), config=SimpleNamespace(**{**vars(good), 'gate_probe_schedule': (8,)}))):
            with self.assertRaises(ContractError):
                plugin.attach(PluginContext(s))

    def test_reaches(self):
        plugin = p2.PermutedPredictor()
        self.assertTrue(plugin.reaches({}))
        self.assertFalse(plugin.reaches({'gate_models': {'gates': [{'entries': 23}, {'entries': 5}]}}))
        self.assertTrue(plugin.reaches({'gate_models': {'gates': [{'entries': 5}, {'entries': 24}]}}))


def forecast(won, hp=0, potions=0, deaths=0, death_turn=None, enemy_hp=0, survives=None, end_turn=5):
    return {'won': won, 'survives': won if survives is None else survives, 'hp': hp, 'potions': potions,
            'death_turn': death_turn, 'end_turn': end_turn, 'enemy_hp': enemy_hp, 'enemy_deaths': deaths}


MEMBERS = [{'mode': 'beam', 'beam': 8, 'nodes': 1000 * (j + 1)} for j in range(6)]
ADVISOR = {'gate_plans': {'Boss': {'members': MEMBERS[:1], 'select': 'best'},
                          'FinalBoss': {'members': MEMBERS, 'select': 'best'}}}
# F1 first call: member 1 deployed; member 2 forecasts the same line; member 3 loses.
F1_CALL = [forecast(True, hp=30), forecast(True, hp=40), forecast(True, hp=40), forecast(False, enemy_hp=20),
           forecast(True, hp=35, potions=1), forecast(True, hp=30, end_turn=7)]
ROWS = [{'gate': (1, 0), 'index': 40, 'lost': None, 'entry': {'floor': 16}},
        {'gate': (2, 0), 'index': 150, 'lost': None, 'entry': {'floor': 33}},
        {'gate': (3, 0), 'index': 300, 'lost': None, 'entry': {'floor': 49}},
        {'gate': (3, 1), 'index': 350, 'lost': (80, 300), 'entry': {'floor': 50}}]


def record(room, encounter, j=0, f=None, selected=False):
    return {'room': room, 'encounter': encounter, 'member_index': j, 'member': dict(MEMBERS[j], widths=[8]),
            'forecast': f or forecast(True), 'selected': selected}


def lost_f2_result():
    searches = [record('Boss', 'B1', selected=True), record('Monster', 'M'), record('Boss', 'B2', selected=True),
                record('Elite', 'E')]
    searches += [record('Boss', 'B3a', j, f, j == 1) for j, f in enumerate(F1_CALL)]
    searches += [record('Boss', 'B3a', 0, forecast(True, hp=38), True)]            # a later call of F1
    searches += [record('Boss', 'B3b', 0, forecast(False, enemy_hp=80), True)]     # F2 directly after F1
    return {'trace': list(range(400)), 'advisor_metrics': {'searches': searches}}


class F2RerollTests(unittest.TestCase):
    def test_key_is_the_host_order(self):
        key = p1._key
        self.assertGreater(key(forecast(True, hp=1)), key(forecast(False, survives=True)))
        self.assertGreater(key(forecast(True, hp=40)), key(forecast(True, hp=30)))
        self.assertGreater(key(forecast(True, hp=30)), key(forecast(True, hp=30, potions=1)))
        self.assertGreater(key(forecast(False, survives=True)), key(forecast(False, death_turn=3, deaths=2)))
        self.assertGreater(key(forecast(False, deaths=1, death_turn=3)), key(forecast(False, death_turn=9)))
        self.assertGreater(key(forecast(False, death_turn=9)), key(forecast(False, death_turn=3)))
        self.assertGreater(key(forecast(False, death_turn=3)), key(forecast(False, death_turn=3, enemy_hp=5)))
        self.assertEqual(key(forecast(True, hp=30)), key(forecast(True, hp=30, end_turn=7)))

    def test_groups_and_first_call(self):
        groups = p1.boss_search_groups(lost_f2_result())
        self.assertEqual([group[0]['encounter'] for group in groups], ['B1', 'B2', 'B3a', 'B3b'])
        self.assertEqual(len(groups[2]), len(F1_CALL) + 1)
        self.assertEqual([r['member_index'] for r in p1.first_call(groups[2])], list(range(len(F1_CALL))))
        self.assertEqual(p1.first_call([record('Boss', 'B', 1)]), [])
        self.assertEqual(p1.first_call([]), [])

    def alternatives(self, rows=ROWS, history=0, limit=8, result=None):
        with patch.object(p1, 'boss_fight_rows', return_value=rows):
            return p1.f1_alternatives(result or lost_f2_result(), history, limit)

    def test_alternatives(self):
        f1, records = self.alternatives()
        self.assertIs(f1, ROWS[2])
        self.assertEqual([r['member_index'] for r in records], [4, 0, 5])   # best first, ties to the earlier member
        self.assertEqual([r['member_index'] for r in self.alternatives(limit=2)[1]], [4, 0])
        # a history past the act 1 boss: that fight was replayed and has no records
        self.assertEqual([r['member_index'] for r in self.alternatives(history=100, result=self.without('B1'))[1]],
                         [4, 0, 5])
        self.assertEqual(self.alternatives(history=301), (None, []))                         # F1 itself replayed

    @staticmethod
    def without(*encounters):
        result = lost_f2_result()
        searches = result['advisor_metrics']['searches']
        result['advisor_metrics']['searches'] = [r for r in searches if r['encounter'] not in encounters]
        return result

    def test_no_alternatives_proposed(self):
        self.assertEqual(self.alternatives(rows=ROWS[:3]), (None, []))                       # nothing lost
        lost_f1 = [dict(row) for row in ROWS[:3]]
        lost_f1[2]['lost'] = (10, 300)
        self.assertEqual(self.alternatives(rows=lost_f1), (None, []))                        # lost at F1
        self.assertEqual(self.alternatives(result=self.without('B1')), (None, []))           # searches do not match fights
        self.assertEqual(self.alternatives(rows=[ROWS[0], ROWS[1], ROWS[3]]), (None, []))    # no F1 row

    def search(self, capabilities=(p1.CAPABILITY,), advisor=ADVISOR, scope=None, stamp_present=True):
        class Ev:
            def __init__(self, advisor):
                self.advisor = advisor
            def request(self, prefix, **kwargs):
                return {'prefix': list(prefix), **kwargs}
        pool = (SimpleNamespace(stamp={'capabilities': list(capabilities)}) if stamp_present
                else SimpleNamespace())
        return PluginContext(SimpleNamespace(
            pool=pool, ev=Ev(advisor), scope_prefix=scope, component=None,
            config=SimpleNamespace(lookahead_floors=99, max_decisions=12000,
                                   lookahead_actions=12000)))

    def test_attach(self):
        plugin = p1.F2Reroll()
        plugin.attach(self.search())
        for s in (self.search(capabilities=()), self.search(advisor=None), self.search(advisor={'gate_plans': {}}),
                  self.search(scope=[1, 2])):
            with self.assertRaises(ContractError):
                plugin.attach(s)
        with self.assertRaises(ContractError):
            plugin.attach(self.search(stamp_present=False))

    def observe(self, plugin, s, classification='NATIVE_ROUTE_DEATH', advisor=ADVISOR):
        with patch.object(p1, 'boss_fight_rows', return_value=ROWS), patch.object(p1, 'utility', return_value=(1, 7)):
            plugin.observe(s, {'request': {'history': [], 'advisor': advisor}}, lost_f2_result(), 'L1',
                           classification, 'family-a')

    def test_observe_proposes_f1_rerolls(self):
        plugin, s = p1.F2Reroll(k=2), self.search()
        self.observe(plugin, s)
        self.assertEqual(len(plugin.queue), 2)
        first = plugin.queue[0]
        self.assertEqual((first['kind'], first['category'], first['priority'], first['family']),
                         ('f2_reroll', 'failure', (1, 7), 'family-a'))
        self.assertEqual(first['repair'], {'source': 'L1', 'floor': 49, 'room': 'Boss', 'member': 4, 'rank': 0})
        request = first['request']
        self.assertEqual(request['prefix'], list(range(300)))
        self.assertEqual((request['policy'], request['horizon'], request['actions']), (0, 148, 12000))
        plans = request['advisor_patch']['gate_plans']
        self.assertEqual(plans['FinalBoss1'], {'members': [MEMBERS[4]], 'select': 'first_win'})
        self.assertEqual({k: v for k, v in plans.items() if k != 'FinalBoss1'}, ADVISOR['gate_plans'])
        self.assertIsNot(plans['FinalBoss1']['members'][0], MEMBERS[4])
        self.assertEqual(plugin.queue[1]['repair']['member'], 0)
        self.assertEqual(plugin.metrics(s), {'f2_losses_seen': 1, 'without_alternative': 0, 'proposed': 2,
                                             'duplicates': 0, 'dispatched': 0, 'pending': 2})
        self.observe(plugin, s)                                                     # the same F1 again
        self.assertEqual(len(plugin.queue), 2)
        self.assertEqual((plugin.counts['f2_losses_seen'], plugin.counts['duplicates']), (2, 2))
        self.observe(plugin, s, classification='NATIVE_WIN_CANDIDATE')
        self.assertEqual(plugin.counts['f2_losses_seen'], 2)

    def test_observe_skips_members_of_another_plan(self):
        plugin = p1.F2Reroll()
        other = {'gate_plans': {'FinalBoss': {'members': [dict(m, nodes=7) for m in MEMBERS], 'select': 'best'}}}
        self.observe(plugin, self.search(), advisor=other)
        self.assertEqual((plugin.queue, plugin.counts['proposed']), ([], 0))

    def test_pop_best_priority_first(self):
        plugin = p1.F2Reroll()
        plugin.queue = [{'id': 'a', 'priority': (1,)}, {'id': 'b', 'priority': (3,)}, {'id': 'c', 'priority': (3,)}]
        self.assertEqual([plugin.pop(0)['id'] for _ in range(3)], ['b', 'c', 'a'])
        self.assertIsNone(plugin.pop(0))
        self.assertEqual(plugin.counts['dispatched'], 3)

    def test_sources(self):
        (source,) = p1.F2Reroll().sources(None)
        self.assertEqual((source.share, source.kinds), (None, p1.KINDS))
        self.assertEqual(p1.F2Reroll(share=25).sources(None)[0].share, .25)

    def test_reaches(self):
        plugin = p1.F2Reroll()
        def snapshot(entries, survived):
            return {'gate_models': {'gates': [{'ordinal': 0, 'entries': 9, 'survived_entries': 3},
                                              {'ordinal': 1, 'entries': entries, 'survived_entries': survived}]}}
        self.assertTrue(plugin.reaches({}))
        self.assertFalse(plugin.reaches(snapshot(1, 1)))
        self.assertFalse(plugin.reaches(snapshot(0, 0)))
        self.assertTrue(plugin.reaches(snapshot(1, 0)))
        self.assertTrue(plugin.reaches(snapshot(2, 2)))


if __name__ == '__main__':
    unittest.main()
