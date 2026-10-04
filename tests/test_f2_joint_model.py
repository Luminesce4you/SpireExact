"""Joint target scaling, source replacement and native-tier compatibility."""
import copy
import math
import unittest

from spire_exact.canonical import canonical
from spire_exact.planning.f2_joint_model import F2JointModel
from spire_exact.planning.gatemodel import GateModels, _Gate


def observation(good=True):
    return {'hp': '40', 'max_hp': '80', 'deck': [{'id': 'STRIKE', 'upgrade': 0},
            {'id': 'GOOD' if good else 'BAD', 'upgrade': 0}], 'relics': [], 'potions': [None, None]}


def win(value=1.4):
    return {'won': True, 'outcome': value, 'lost': None}


def loss(removed=20.0, total=100.0):
    return {'won': False, 'outcome': None, 'lost': (removed, total)}


def fit_joint(model=None, count=24):
    model = model or F2JointModel()
    for index in range(count):
        good = index % 2 == 0
        raw = {'outcome': 1.4, 'lost': None} if good else {'outcome': .2, 'lost': (20.0, 100.0)}
        model.observe(f'entry-{index}'.encode(), observation(good), raw,
                      [win(1.4) if good else loss()] * 5, sample_group_key=f'group-{index}'.encode())
    return model


def real_gate(z, *, fitted=24, size_z=0.0, probes=None):
    gate = _Gate()
    gate.fitted = fitted
    gate.z = dict(z)
    gate.size_z = size_z
    gate.probe_z = dict(probes or {})
    return gate


class JointTargets(unittest.TestCase):
    def test_two_gates_use_separate_common_life_totals_without_extra_weights(self):
        model = F2JointModel()
        model.observe(b'a', observation(), {'outcome': .8, 'lost': (80.0, 100.0)}, [loss(20, 100)] * 5)
        self.assertAlmostEqual(model.target(b'a'), 1.0)
        model.observe(b'b', observation(False), {'outcome': .5, 'lost': (100.0, 200.0)}, [loss(50, 500)] * 5)
        self.assertAlmostEqual(model.target(b'a'), .4 + .04)
        self.assertAlmostEqual(model.target(b'b'), .5 + .1)
        model.set_life_totals(f1_life_total=400, f2_life_total=1000)
        self.assertAlmostEqual(model.target(b'a'), .2 + .02)

    def test_real_f1_features_keep_actual_entry_hp(self):
        model = F2JointModel()
        model.observe(b'a', observation(), {'outcome': 1.25, 'lost': None}, [win(1.4)] * 5)
        self.assertEqual(model.entries[b'a'].features['#hp'], .5)
        self.assertAlmostEqual(model.target(b'a'), 2.65)

    def test_incomplete_or_invalid_samples_and_unknown_real_loss_are_not_zero_rows(self):
        model = F2JointModel()
        for index, outcomes in enumerate(([loss()] * 4, [loss()] * 4 + [None],
                                          [loss()] * 4 + [loss(0, 0)])):
            self.assertFalse(model.observe(str(index).encode(), observation(), {'outcome': .2, 'lost': (20, 100)}, outcomes))
        self.assertFalse(model.observe(b'unknown-f1', observation(), {'outcome': 0, 'lost': (0, 0)}, [loss()] * 5))
        self.assertEqual(len(model.entries), 0)
        self.assertIsNone(model.predict(observation()))

    def test_duplicate_and_five_to_ten_updates_compare_finite_statistics_without_native_encoder(self):
        model = F2JointModel()
        raw = {'outcome': .2, 'lost': (20.0, 100.0)}
        self.assertTrue(model.observe(b'a', observation(), raw, [loss()] * 5))
        equivalent = [dict(loss(), lost=[20.0, 100.0], search_seconds=3.14)] * 5
        self.assertFalse(model.observe(b'a', observation(), copy.deepcopy(raw), equivalent))
        self.assertTrue(model.observe(b'a', observation(), raw, [loss()] * 5 + [loss(40, 100)] * 5))
        self.assertEqual(len(model.entries), 1)
        self.assertAlmostEqual(model.target(b'a'), .2 + .3)

    def test_same_real_entry_uses_best_real_f1_outcome(self):
        model = F2JointModel()
        model.observe(b'a', observation(), {'outcome': .2, 'lost': (20, 100)}, [loss()] * 5)
        model.observe(b'a', observation(), {'outcome': .7, 'lost': (70, 100)}, [loss()] * 5)
        self.assertAlmostEqual(model.target(b'a'), .9)
        model.observe(b'a', observation(), {'outcome': .1, 'lost': (10, 100)}, [loss()] * 5)
        self.assertAlmostEqual(model.target(b'a'), .9)
        model.observe(b'a', observation(), {'outcome': 1.5, 'lost': None}, [loss()] * 5)
        self.assertAlmostEqual(model.target(b'a'), 1.7)

    def test_first_fit_at_24_and_refresh_after_16_additional_real_entries(self):
        model = fit_joint(count=23)
        self.assertEqual(model.fitted, 0)
        self.assertIsNone(model.predict(observation()))
        model.observe(b'entry-23', observation(False), {'outcome': .2, 'lost': (20, 100)}, [loss()] * 5)
        self.assertEqual(model.fitted, 24)
        for index in range(24, 39):
            model.observe(f'entry-{index}'.encode(), observation(), {'outcome': 1.4, 'lost': None}, [win()] * 5)
        self.assertEqual(model.fitted, 24)
        model.observe(b'entry-39', observation(), {'outcome': 1.4, 'lost': None}, [win()] * 5)
        self.assertEqual(model.fitted, 40)
        self.assertGreater(model.predict(observation(True)), model.predict(observation(False)))

    def test_shared_samples_are_reported_as_groups_and_not_independent_measurements(self):
        model = F2JointModel()
        for index in range(24):
            model.observe(f'e-{index}'.encode(), observation(), {'outcome': 1.3, 'lost': None},
                          [loss()] * 5, sample_group_key=b'one-shared-group')
        snapshot = model.snapshot()
        self.assertEqual((snapshot['entries'], snapshot['sample_groups']), (24, 1))
        self.assertIn('correlated', snapshot['entry_count_is'])


class GateContributionTests(unittest.TestCase):
    def test_unfitted_joint_preserves_real_z_prior_and_real_regression_data(self):
        model = fit_joint(count=23)
        control = GateModels(7)
        adapted = GateModels(7, f2_joint_model=model)
        for candidate in (control, adapted):
            candidate.gates[(2, 0)] = real_gate({'card:GOOD': 3.0})
            candidate.gates[(2, 1)] = real_gate({'card:BAD': -2.0})
        self.assertEqual(canonical(control.prior(19)), canonical(adapted.prior(19)))
        self.assertEqual(adapted.gates[(2, 0)].z, {'card:GOOD': 3.0})
        self.assertEqual(adapted.gates[(2, 0)].entries, {})

    def test_fitted_joint_replaces_only_f1_while_real_f2_contributes_with_existing_sqrt(self):
        model = fit_joint()
        adapted = GateModels(7, f2_joint_model=model)
        first = real_gate({'card:GOOD': -100.0})
        second = real_gate({'card:GOOD': 6.0})
        adapted.gates[(2, 0)], adapted.gates[(2, 1)] = first, second
        self.assertAlmostEqual(adapted.z('card:GOOD', 2), (model.z['card:GOOD'] + 6.0) / math.sqrt(2))
        self.assertEqual(first.z, {'card:GOOD': -100.0})
        self.assertIs(adapted.gates[(2, 1)], second)
        self.assertEqual((first.entries, second.entries), ({}, {}))

    def test_joint_preserves_old_optional_size_and_probe_extras(self):
        model = fit_joint()
        adapted = GateModels(7, size=True, probes=True, f2_joint_model=model)
        adapted.gates[(2, 0)] = real_gate({'card:GOOD': -100}, size_z=2.5, probes={'card:GOOD': 8.0})
        self.assertAlmostEqual(adapted.z('card:GOOD', 2), .5 * (model.z['card:GOOD'] + 8.0))
        self.assertEqual(adapted.z('card_skip', 2), -2.5)

    def test_fitting_invalidates_zero_cache_and_uses_unchanged_tier_noise_mapping(self):
        model = F2JointModel()
        adapted = GateModels(271828, f2_joint_model=model)
        self.assertEqual(adapted.z('card:GOOD', 2), 0)
        self.assertEqual(adapted.labels(), [])
        fit_joint(model)
        self.assertGreater(adapted.z('card:GOOD', 2), 0)
        reference = GateModels(271828)
        reference.gates[(2, 0)] = real_gate(model.z)
        self.assertEqual(canonical(adapted.prior(9)), canonical(reference.prior(9)))
        self.assertEqual(canonical(adapted.prior(9)), canonical(adapted.prior(9)))
        self.assertEqual(adapted.snapshot()['f2_joint_model']['replaces_gate'], [2, 0])


if __name__ == '__main__':
    unittest.main()
