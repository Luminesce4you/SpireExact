"""F1-only alternating readiness places; exact entry keys, no game runs."""
import copy
import unittest

from spire_exact.canonical import canonical
from spire_exact.planning.archive import failure_combat_prefix
from spire_exact.planning.focus import FocusScheduler
from spire_exact.planning.gatemodel import boss_fight_rows
from tests.test_focus_lineage import campaign


def source(tag=0, enemy_hp=30, stage='f1', deck=None):
    result = campaign(variant=tag % 3, stage=stage)
    result['trace'][2] = {'kind': 'end_turn', 'combat_variant': tag}
    f1 = next((row for row in boss_fight_rows(result) if row['gate'] == (2, 0)), None)
    if f1 is not None:
        index = f1['index']
        action = {'kind': 'map', 'index': 0}
        result['trace'].insert(index, action)
        result['decision_evidence'].insert(index, {'phase': 'map',
            'observation': {'act': 2, 'floor': 47, 'hp': '80', 'max_hp': '80'},
            'available_actions': [action, {'kind': 'map', 'index': 1}]})
    result['terminal_combat']['enemies'][0]['hp'] = str(enemy_hp)
    if deck is not None:
        result['observation']['deck'] = [{'id': card, 'upgrade': 0} for card in deck]
    return result


def fill(scheduler):
    for index, (name, hp) in enumerate((('best-dead', 10), ('middle-unknown', 20),
                                       ('viable-low', 80), ('viable-high', 90))):
        scheduler.add(source(index, hp), name)


def signal(scheduler):
    scheduler.record_f2_readiness('best-dead', 'DEAD_AT_FULL_HP', mean=.2)
    scheduler.record_f2_readiness('viable-low', 'VIABLE', mean=1.1)
    scheduler.record_f2_readiness('viable-high', 'VIABLE', mean=1.4)


class JointFocusPlaces(unittest.TestCase):
    def test_two_orders_alternate_places_without_wholesale_replacing_quality(self):
        scheduler = FocusScheduler(elites=4, explore=0, f2_joint_focus=True)
        fill(scheduler)
        signal(scheduler)
        self.assertEqual(scheduler._places(), ['best-dead', 'viable-high', 'middle-unknown', 'viable-low'])
        self.assertEqual([row[2] for row in scheduler.pool],
                         ['best-dead', 'middle-unknown', 'viable-low', 'viable-high'])
        report = scheduler.snapshot()['f2_joint_focus']
        self.assertEqual(report['elites'][0]['status'], 'DEAD_AT_FULL_HP')
        self.assertTrue(report['allocation_only'])
        self.assertFalse(report['pruning'])

    def test_pending_and_unknown_keep_the_existing_quality_order(self):
        scheduler = FocusScheduler(elites=4, f2_joint_focus=True)
        fill(scheduler)
        scheduler.record_f2_readiness('best-dead', 'UNKNOWN')
        scheduler.record_f2_readiness('middle-unknown', 'PENDING', mean=.4)
        self.assertEqual(scheduler._places(), [row[2] for row in scheduler.pool])

    def test_viable_means_sort_high_first_then_quality_breaks_equal_means(self):
        scheduler = FocusScheduler(f2_joint_focus=True)
        fill(scheduler)
        for label in ('best-dead', 'viable-low'):
            scheduler.record_f2_readiness(label, 'VIABLE', mean=1.1)
        scheduler.record_f2_readiness('viable-high', 'VIABLE', mean=1.4)
        labels = [row[2] for row in scheduler.pool]
        self.assertEqual(scheduler._joint_places(labels),
                         ['viable-high', 'best-dead', 'viable-low', 'middle-unknown'])

    def test_other_death_locations_and_f2_deaths_never_enter_the_alternate_f1_order(self):
        scheduler = FocusScheduler(elites=6, f2_joint_focus=True)
        fill(scheduler)
        scheduler.add(source(7, 30, stage='f2'), 'second-boss')
        scheduler.add(source(8, 40, stage='corridor'), 'hallway')
        signal(scheduler)
        labels = [row[2] for row in scheduler.pool]
        alternate = scheduler._joint_places(labels)
        for label in ('second-boss', 'hallway'):
            self.assertEqual(labels.index(label), alternate.index(label))
            self.assertIsNone(scheduler.sources[label].f1_entry)

    def test_pool_eviction_and_deck_cluster_rules_are_the_same(self):
        ordinary = FocusScheduler(elites=2, pool=3, cluster_cap=1)
        joint = FocusScheduler(elites=2, pool=3, cluster_cap=1, f2_joint_focus=True)
        for index in range(7):
            result = source(index, 10 + index * 10, deck=['A'] * 10 if index < 5 else ['B'] * 10)
            for scheduler in (ordinary, joint):
                scheduler.add(copy.deepcopy(result), str(index))
        self.assertEqual(ordinary.pool, joint.pool)
        self.assertEqual(ordinary.crowded_out, joint.crowded_out)
        for label in joint.sources:
            joint.record_f2_readiness(label, 'VIABLE' if label == '5' else 'DEAD_AT_FULL_HP', mean=1.4)
        elites = joint._elite_sources()
        self.assertEqual(len(elites), len(ordinary._elite_sources()))
        self.assertEqual(len({tuple(sorted(source.deck.items())) for source in elites}), len(elites))

    def test_readiness_updates_do_not_remove_any_pending_branch(self):
        scheduler = FocusScheduler(elites=4, f2_joint_focus=True)
        fill(scheduler)
        before = set(scheduler.explorer.branches)
        signal(scheduler)
        self.assertEqual(set(scheduler.explorer.branches), before)
        seen = set()
        while (proposal := scheduler.next()) is not None:
            seen.add(canonical(proposal['prefix']))
        ordinary = FocusScheduler(elites=4)
        fill(ordinary)
        expected = set()
        while (proposal := ordinary.next()) is not None:
            expected.add(canonical(proposal['prefix']))
        self.assertEqual(seen, expected)

    def test_same_entry_retry_and_elite_bind_the_exact_prefix_including_enter_map(self):
        scheduler = FocusScheduler(f2_joint_focus=True)
        result = source()
        scheduler.add(result, 'original')
        expected = canonical(failure_combat_prefix(result)['prefix'])
        self.assertEqual(scheduler.f1_entry_key('original'), expected)
        self.assertEqual(failure_combat_prefix(result)['prefix'][-1]['kind'], 'map')
        stronger = copy.deepcopy(result)
        stronger['terminal_combat']['enemies'][0]['hp'] = '20'
        scheduler.add(stronger, 'retry')
        self.assertEqual(scheduler.f1_entry_key('retry'), expected)
        scheduler.record_f2_readiness('original', 'DEAD_AT_FULL_HP', mean=.3)
        self.assertEqual(scheduler.f2_readiness[scheduler.sources['retry'].f1_entry]['status'], 'DEAD_AT_FULL_HP')
        self.assertFalse(scheduler.record_f2_readiness('original', 'VIABLE', mean=1.3, entry_key=b'another-entry'))

    def test_missing_signal_unknown_sources_and_synthetic_labels_are_not_guessed(self):
        scheduler = FocusScheduler(f2_joint_focus=True)
        result = source()
        result['synthetic'] = True
        scheduler.add(result, 'synthetic')
        self.assertIsNone(scheduler.f1_entry_key('synthetic'))
        self.assertFalse(scheduler.record_f2_readiness('missing', 'VIABLE', mean=1.2))
        self.assertFalse(scheduler.record_f2_readiness('synthetic', 'DEAD_AT_FULL_HP', mean=.1))

    def test_without_any_readiness_information_requests_are_byte_identical(self):
        for options in ({}, {'cluster_cap': 2}, {'stall': 3}):
            with self.subTest(options=options):
                ordinary = FocusScheduler(elites=4, **options)
                joint = FocusScheduler(elites=4, f2_joint_focus=True, **options)
                fill(ordinary)
                fill(joint)
                expected, actual = [], []
                while (proposal := ordinary.next()) is not None:
                    expected.append(canonical(proposal))
                while (proposal := joint.next()) is not None:
                    actual.append(canonical(proposal))
                self.assertEqual(actual, expected)
                self.assertEqual(joint.joint_place_changes, 0)

    def test_flag_off_ignores_readiness_and_keeps_requests_identical(self):
        default = FocusScheduler(elites=4)
        explicit = FocusScheduler(elites=4, f2_joint_focus=False)
        fill(default)
        fill(explicit)
        self.assertFalse(explicit.record_f2_readiness('best-dead', 'DEAD_AT_FULL_HP', mean=.2))
        expected, actual = [], []
        while (proposal := default.next()) is not None:
            expected.append(canonical(proposal))
        while (proposal := explicit.next()) is not None:
            actual.append(canonical(proposal))
        self.assertEqual(actual, expected)

    def test_joint_focus_and_carry_are_explicitly_incompatible(self):
        with self.assertRaises(ValueError):
            FocusScheduler(f2_joint_focus=True, carry=True)
        with self.assertRaises(ValueError):
            FocusScheduler(f2_joint_focus=1)


if __name__ == '__main__':
    unittest.main()
