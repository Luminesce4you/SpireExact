"""i082: denser / deduplicated / mean-merged paired card tables, their add-card
arms as joint-model rows, and one --feature-profile carrying every mechanism."""
import argparse
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spire_exact.planning import __main__ as planner
from spire_exact.planning.final_defaults import (FEATURE_PROFILES, FINAL_FEATURES, I081_FEATURES, I082_PLANNER,
                                                 i082_argv, resolve_entry_defaults)
from spire_exact.planning.f2_joint_model import F2JointModel
from spire_exact.planning.paired_card_probes import PairedCardProbes
from spire_exact.planning.search import SearchConfig
from tests.test_paired_card_probes import CTX, TEMPLATE, trajectory, loss
from tools.run_seed import PROFILES, feature_argv

I080_JOB = ['--low-hp-routes-any-act', '--focus-stall-extended', '--focus-stall-f2', '--forge-menu-dedup', 'exact',
            '--runtime-profile', 'server-bounded-large-gen0', '--root-policies', 'pick,elo', '--focus-cluster-cap', '2',
            '--final-gate-plan', 'open', '--root-async', '--root-round', '7', '--requeue-lost', '1',
            '--gate-preset', 'escalate-evaluate', '--focus-stall', '32']


def entry(serial, extra_card=None):
    result = trajectory()
    result['trace'][0]['entry_serial'] = serial
    if extra_card:
        result['decision_evidence'][3]['observation']['deck'].append({'id': extra_card, 'upgrade': 0})
    return result


def fill(probes, specs, removed):
    for spec in specs:
        probes.absorb(spec, [loss(value) for value in removed])


class PairedScheduleTests(unittest.TestCase):
    def test_defaults_are_the_former_behaviour(self):
        old = PairedCardProbes(True, 123, context=CTX, inputs={'game': 'fingerprint'})
        new = PairedCardProbes(True, 123, context=CTX, inputs={'game': 'fingerprint'}, every=0, dedup=False, merge='latest')
        self.assertEqual(old.observe(trajectory(), 'real', 0, TEMPLATE), new.observe(trajectory(), 'real', 0, TEMPLATE))
        self.assertEqual(old.snapshot()['entry_schedule'], {'first': 1, 'second': 32, 'factor': 4})
        for bad in ({'every': -1}, {'every': True}, {'dedup': 1}, {'merge': 'max'}):
            with self.assertRaises(ValueError):
                PairedCardProbes(True, 1, **bad)

    def test_every_n_distinct_entries(self):
        probes = PairedCardProbes(True, 123, context=CTX, every=32)
        scheduled = [count for count in range(1, 129) if probes.observe(entry(count), str(count), 0, TEMPLATE)]
        self.assertEqual(scheduled, [1, 32, 64, 96, 128])
        snap = probes.snapshot()
        self.assertEqual(snap['next_entry_threshold'], 160)
        self.assertEqual(snap['entry_schedule'], {'first': 1, 'every': 32})

    def test_duplicate_table_is_skipped_and_threshold_stays_due(self):
        probes = PairedCardProbes(True, 123, context=CTX, every=2, dedup=True)
        scheduled = [count for count, result in ((1, entry(1)), (2, entry(2)), (3, entry(3, 'EXTRA')))
                     if probes.observe(result, str(count), 0, TEMPLATE)]
        self.assertEqual(scheduled, [1, 3])
        snap = probes.snapshot()
        self.assertEqual((snap['next_entry_threshold'], snap['duplicate_tables_skipped'], snap['dedup']), (4, 1, True))
        plain = PairedCardProbes(True, 123, context=CTX, every=2)
        self.assertEqual([c for c in (1, 2, 3) if plain.observe(entry(c), str(c), 0, TEMPLATE)], [1, 2])

    def test_mean_merge_weighs_each_complete_table_once(self):
        def run(merge, partial=False):
            probes = PairedCardProbes(True, 123, context=CTX, every=1, merge=merge)
            first = probes.observe(entry(1), 'one', 0, TEMPLATE)
            second = probes.observe(entry(2), 'two', 0, TEMPLATE)
            fill(probes, first, (50, 20, 90, 55))
            fill(probes, second[:4] if partial else second, (50, 80, 10, 55))
            return probes
        mean = run('mean')
        for name, value in (('card_skip', 0), ('card:BAD', 0), ('card:GOOD', 0), ('card:MEH', 0.05)):
            self.assertAlmostEqual(mean.signals[name], value)
        self.assertEqual(mean.snapshot()['signal_tables']['card:MEH'], 2)
        latest = run('latest')
        self.assertAlmostEqual(latest.signals['card:BAD'], 0.3)
        self.assertAlmostEqual(latest.signals['card:GOOD'], -0.4)
        partial = run('mean', partial=True)
        self.assertAlmostEqual(partial.signals['card:BAD'], -0.3)
        self.assertAlmostEqual(partial.signals['card:GOOD'], 0.4)


class PairedJointRowTests(unittest.TestCase):
    def setUp(self):
        self.probes = PairedCardProbes(True, 123, context=CTX, inputs={'game': 'fingerprint'})
        result = trajectory()
        fill(self.probes, self.probes.observe(result, 'source', 0, TEMPLATE), (50, 20, 90, 55))
        table = self.probes.tables[0]
        self.base = {'rawentrykey': b'real-entry', 'observation': copy.deepcopy(result['decision_evidence'][3]['observation']),
                     'f1_raw': {'won': False, 'outcome': None, 'lost': (30.0, 100.0)}, 'f1_life_total': 100.0,
                     'f2_life_total': 100.0, 'sample_group_key': b'group',
                     'sources': [{'label': 'source', 'index': table['entry_index']}]}

    def test_add_card_arms_become_joint_rows(self):
        rows = self.probes.joint_rows(lambda label: self.base if label == 'source' else None)
        self.assertEqual(len(rows), 3)
        bad = rows[0]
        self.assertEqual(bad['entry_key'], b'real-entry\x00paired-card/v1:card:BAD')
        self.assertEqual(bad['observation']['deck'][-1], {'id': 'BAD', 'upgrade': 0})
        self.assertEqual(len(bad['observation']['deck']), len(self.base['observation']['deck']) + 1)
        self.assertEqual([row['lost'][0] for row in bad['f2_outcomes']], [20.0] * 5)
        self.assertEqual(bad['f1_raw'], self.base['f1_raw'])
        self.assertEqual(bad['sample_group_key'], bad['entry_key'])
        model = F2JointModel()
        for row in rows:
            self.assertTrue(model.observe(row['entry_key'], row['observation'], row['f1_raw'], row['f2_outcomes'],
                f1_life_total=row['f1_life_total'], f2_life_total=row['f2_life_total'], sample_group_key=row['sample_group_key']))
        self.assertEqual(self.probes.snapshot()['joint_rows'], 3)

    def test_table_without_matching_base_is_skipped(self):
        other = copy.deepcopy(self.base)
        other['sources'][0]['index'] += 1
        self.assertEqual(self.probes.joint_rows(lambda label: other), [])
        self.assertEqual(self.probes.joint_rows(lambda label: None), [])
        self.assertEqual(self.probes.snapshot()['joint_tables_without_unique_base'], 1)

    def test_partial_table_gives_no_rows(self):
        probes = PairedCardProbes(True, 123, context=CTX)
        fill(probes, probes.observe(trajectory(), 'source', 0, TEMPLATE)[:4], (50, 20, 90, 55))
        self.assertEqual(probes.joint_rows(lambda label: self.base), [])


class ConfigTests(unittest.TestCase):
    def test_search_config_validation(self):
        SearchConfig(paired_card_every=32, paired_card_dedup=True, paired_card_merge='mean')
        for bad in ({'paired_card_every': -1}, {'paired_card_dedup': 1}, {'paired_card_merge': 'max'},
                    {'paired_card_joint': True}):
            with self.assertRaises(ValueError):
                SearchConfig(**bad)

    def namespace(self, profile, **explicit):
        args = {key: None for key in (*FINAL_FEATURES, *I081_FEATURES, *I082_PLANNER)}
        args.update(feature_profile=profile, ascension=None, nodes=None, paired_card_probes=True)
        args.update(explicit)
        return resolve_entry_defaults(argparse.Namespace(**args))

    def test_i082_resolves_every_value(self):
        a = self.namespace('i082')
        for key, (value, _) in I082_PLANNER.items():
            self.assertEqual(getattr(a, key), value, key)
        for key in (*FINAL_FEATURES, *I081_FEATURES):
            self.assertIs(getattr(a, key), True, key)
        self.assertEqual((a.ascension, a.nodes), (10, 60000))
        a = self.namespace('i082', focus_stall_f2=False, gate_preset='escalate', f2_joint_model=False)
        self.assertEqual((a.focus_stall_f2, a.gate_preset, a.f2_joint_model, a.paired_card_joint),
                         (False, 'escalate', False, False))
        self.assertIs(self.namespace('i082', paired_card_probes=False).paired_card_joint, False)

    def test_other_profiles_keep_former_defaults(self):
        for profile in ('i081', 'i075-final', 'legacy'):
            a = self.namespace(profile)
            for key, (_, other) in I082_PLANNER.items():
                self.assertEqual(getattr(a, key), other, (profile, key))
        self.assertEqual(FEATURE_PROFILES[0], 'i082')

    def test_argv_renders_everything_but_the_derived_joint_switch(self):
        argv = i082_argv()
        self.assertNotIn('--paired-card-joint', argv)
        self.assertIn('--f2-joint-model', argv)
        self.assertEqual(argv[argv.index('--paired-card-merge') + 1], 'mean')
        self.assertEqual(feature_argv(None), [])
        self.assertEqual(feature_argv('i081'), ['--feature-profile', 'i081'])
        self.assertEqual(feature_argv('i082'), ['--feature-profile', 'i082', *argv])


class PlannerParseTests(unittest.TestCase):
    def parse(self, argv):
        seen = {}
        def spy(args):
            seen['args'] = resolve_entry_defaults(args)
            return seen['args']
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'old').write_text('x')
            with patch.object(planner, 'resolve_entry_defaults', spy), patch('sys.stderr'):
                with self.assertRaises(SystemExit):
                    planner.main(['--out', tmp, *argv])
        return seen['args']

    def test_default_profile_is_i082_and_passes_validation_to_the_output_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'old').write_text('x')
            with patch('sys.stderr') as err, self.assertRaises(SystemExit):
                planner.main(['--out', tmp])
            text = ''.join(call.args[0] for call in err.write.call_args_list)
        self.assertIn('output must be new/empty', text)
        a = self.parse([])
        self.assertEqual((a.feature_profile, a.scheduler, a.gate_preset, a.paired_card_joint), ('i082', 'focus', 'escalate-evaluate', True))

    def test_run_seed_argv_order_lets_explicit_flags_win(self):
        a = self.parse([*PROFILES['focus'], *feature_argv('i082'), '--no-focus-stall-f2', '--gate-preset', 'escalate'])
        self.assertEqual((a.focus_stall_f2, a.gate_preset), (False, 'escalate'))
        self.assertEqual((a.runtime_profile, a.worker_memory_mib, a.paired_card_every), ('server-bounded-large-gen0', 1792, 32))

    def test_i081_parse_keeps_former_defaults(self):
        a = self.parse(['--feature-profile', 'i081'])
        self.assertEqual((a.scheduler, a.gate_preset, a.runtime_profile, a.worker_memory_mib, a.prior, a.root_async),
                         ('weighted', 'none', 'legacy', 900, False, False))
        self.assertEqual((a.paired_card_every, a.paired_card_dedup, a.paired_card_merge, a.paired_card_joint), (0, False, 'latest', False))
        self.assertIs(a.f2_joint_model, True)

    def test_dropping_the_joint_model_drops_the_derived_switch(self):
        a = self.parse(['--no-f2-joint-model'])
        self.assertEqual((a.f2_joint_model, a.paired_card_joint), (False, False))

    def test_i080_job_equals_i082_on_every_shared_setting(self):
        job = vars(self.parse([*PROFILES['focus'], '--feature-profile', 'i075-final', *I080_JOB,
                               '--f2-readiness-probes', '--f2-dead-retry', '--f2-joint-focus', '--f2-joint-model']))
        new = vars(self.parse([*PROFILES['focus'], *feature_argv('i082')]))
        skip = {'feature_profile', 'out', 'paired_card_every', 'paired_card_dedup', 'paired_card_merge', 'paired_card_joint'}
        self.assertEqual({k: v for k, v in job.items() if k not in skip}, {k: v for k, v in new.items() if k not in skip})


if __name__ == '__main__':
    unittest.main()
