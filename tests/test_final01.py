"""i085-final01: gate clinic, clinic focus, governor, priors and profile.

Fabricated trajectories and synthetic campaign worlds only; never native evidence.
"""
import json
from types import SimpleNamespace
import unittest

from spire_exact.canonical import canonical
from spire_exact.planning.aux_governor import AUX_KINDS, AuxGovernor
from spire_exact.planning.clinic_focus import ClinicFocusScheduler
from spire_exact.planning.final_defaults import FINAL01_PROFILE, i085_final01_argv, resolve_entry_defaults
from spire_exact.planning.gate_clinic import GateClinic
from spire_exact.planning.paired_card_probes import PairedCardProbes
from spire_exact.planning.repairs import RepairQueue
from spire_exact.planning.search import SearchConfig
from spire_exact.planning.strategy_priors import BUILTIN, LabelEffects, StructuralPrior, features


def snapshot(scaling=0.0, strength=0.0, deck=20, damage=4.0, block=3.0, burden=0, upgrades=0.2):
    f = lambda x: repr(float(x))
    return {'schema': 'strategic-capability/v1', 'deck_size': deck, 'damage_per_draw': f(damage),
            'block_per_draw': f(block), 'steady_block_per_draw': f(block), 'draw_per_card': f(0.1),
            'energy_per_card': f(0.0), 'scaling': f(scaling), 'strength': f(strength), 'burden': burden,
            'upgrade_density': f(upgrades)}


def obs(act, floor, hp=60, room=None, turn=None, enemies=None, strategic=None):
    return {'act': act, 'floor': floor, 'room': room, 'hp': str(hp), 'max_hp': '80', 'gold': 50,
            'deck': [{'id': 'STRIKE', 'upgrade': 0}], 'relics': ['BURNING_BLOOD'], 'potions': [None],
            'strategic': strategic or snapshot(), 'turn': turn, 'enemies': enemies}


def campaign(stem=(0, 0), local=0, f2_removed=200.0, total=400.0, f2_hp=40, passed=False, scaling=0.0,
             options=3):
    """Act 1 and act 2 decisions (stem), one act 3 decision (local), F1 passed, F2 fought."""
    trace, evidence = [], []
    strategic = snapshot(scaling=scaling)

    def decide(act, floor, phase, chosen, hp=60):
        actions = [{'kind': phase, 'index': i, 'act': act, 'floor': floor} for i in range(max(options, chosen + 1))]
        trace.append(actions[chosen])
        evidence.append({'phase': phase, 'observation': obs(act, floor, hp, strategic=strategic),
                         'available_actions': actions,
                         'option_labels': [['card:C%d_%d' % (floor, i)] for i in range(options)]})

    decide(0, 5, 'card_reward', stem[0])
    decide(1, 20, 'card_reward', stem[1])
    decide(2, 40, 'map', local)
    move = {'kind': 'map', 'col': 0, 'row': 15}
    trace.append(move)
    evidence.append({'phase': 'map', 'observation': obs(2, 47, 60, strategic=strategic), 'available_actions': [move]})
    enemy = lambda hp, name: [{'id': name, 'combat_id': 1, 'hp': str(hp)}]
    trace.append({'kind': 'end_turn', 'f': 1})
    evidence.append({'phase': 'combat', 'observation': obs(2, 48, 60, 'Boss', 1, enemy(300, 'F1'), strategic),
                     'available_actions': [{'kind': 'end_turn', 'f': 1}]})
    move2 = {'kind': 'map', 'col': 0, 'row': 16}
    trace.append(move2)
    evidence.append({'phase': 'map', 'observation': obs(2, 49, f2_hp, strategic=strategic), 'available_actions': [move2]})
    trace.append({'kind': 'end_turn', 'f': 2})
    evidence.append({'phase': 'combat', 'observation': obs(2, 49, f2_hp, 'Boss', 1, enemy(total, 'F2'), strategic),
                     'available_actions': [{'kind': 'end_turn', 'f': 2}]})
    if passed:
        return {'status': 'TERMINAL', 'value': [1], 'trace': trace, 'decision_evidence': evidence,
                'observation': obs(2, 49, 20, strategic=strategic)}
    end = obs(2, 49, f2_hp, 'Boss', 6, enemy(total - f2_removed, 'F2'), strategic)
    trace.append({'kind': 'end_turn', 'f': 3})
    evidence.append({'phase': 'combat', 'observation': end, 'available_actions': [{'kind': 'end_turn', 'f': 3}]})
    return {'status': 'TERMINAL', 'value': [0], 'trace': trace, 'decision_evidence': evidence,
            'terminal_combat': {'act': 2, 'floor': 49, 'turn': 7,
                                'enemies': [{'id': 'F2', 'combat_id': 1, 'hp': str(total - f2_removed)}]},
            'observation': dict(end, hp='0')}


class StrategyPriorTests(unittest.TestCase):
    def test_features_are_structural_and_degrade_on_unknown_schema(self):
        row = features(obs(2, 48, 40, strategic=snapshot(scaling=1, strength=2)))
        self.assertEqual(row['scaling_total'], 3.0)
        self.assertAlmostEqual(row['hp_fraction'], 0.5)
        other = features(dict(obs(2, 48, 40), strategic={'schema': 'future/v9', 'scaling': '5'}))
        self.assertEqual(set(other), {'hp_fraction'})
        self.assertEqual(features(None), {})

    def test_no_names_in_builtin_prior_and_bad_tables_rejected(self):
        text = json.dumps(BUILTIN['classes'])
        self.assertNotIn('card:', text)
        with self.assertRaises(ValueError):
            StructuralPrior({'schema': 'other'})
        with self.assertRaises(ValueError):
            StructuralPrior({**BUILTIN, 'feature_schema': 'strategic-capability/v2'})

    def test_count_targets_and_population_terms(self):
        prior = StructuralPrior()
        entry = features(obs(2, 48, 30, strategic=snapshot(scaling=0, burden=1)))
        score, rows = prior.deficit(entry, 'final_gauntlet', [])
        names = {name for name, _ in rows}
        self.assertTrue({'scaling_total', 'burden', 'hp_fraction'} <= names)
        self.assertGreater(score, 0.3)
        healthy = features(obs(2, 48, 70, strategic=snapshot(scaling=2, strength=1, upgrades=0.5)))
        self.assertLess(prior.deficit(healthy, 'final_gauntlet', [])[0], score)

    def test_posterior_starts_at_prior_and_follows_the_seed(self):
        prior = StructuralPrior(minimum=8)
        self.assertEqual(prior.posterior('final_gauntlet', []), prior.weights('final_gauntlet'))
        rows = []
        for i in range(30):
            scale = i % 4
            entry = features(obs(2, 48, 60, strategic=snapshot(scaling=scale, damage=4 + (i % 3), block=3 + (i % 5) * 0.1)))
            rows.append((entry, 0.2 - 0.05 * scale))     # this seed punishes scaling
        fitted = prior.posterior('final_gauntlet', rows)
        self.assertLess(fitted['scaling_total'], 0.0)
        self.assertTrue(prior.fits['final_gauntlet']['fitted'])

    def test_option_scores_and_label_effects(self):
        prior = StructuralPrior()
        gain = prior.option_score({'scaling_total': 1.0}, 'final_gauntlet', [('scaling_total', 1.0)])
        plain = prior.option_score({'scaling_total': 1.0}, 'final_gauntlet', [])
        self.assertGreater(gain, plain)
        self.assertGreater(plain, 0)
        effects = LabelEffects()
        result = campaign()
        result['decision_evidence'][1]['observation']['strategic'] = snapshot(scaling=1)
        self.assertGreater(effects.observe(result), 0)
        self.assertEqual(effects.effect('card:C5_0')['scaling_total'], 1.0)


class GateClinicTests(unittest.TestCase):
    def feed(self, clinic, rows, start=0):
        for index, result in enumerate(rows):
            clinic.observe(result, 'r%d' % (start + index))

    def test_stems_are_exact_pre_act_decisions(self):
        clinic = GateClinic()
        a, b, c = campaign((0, 0), 0), campaign((0, 0), 1), campaign((0, 1), 0)
        for label, result in (('a', a), ('b', b), ('c', c)):
            clinic.observe(result, label)
        self.assertEqual(clinic.source('a'), clinic.source('b'))
        self.assertNotEqual(clinic.source('a'), clinic.source('c'))
        self.assertEqual(clinic.source('a')[1], (2, 1))

    def test_insufficient_entries_then_root_on_saturated_far_progress(self):
        clinic = GateClinic(min_entries=6)
        self.feed(clinic, [campaign((0, 0), i, 200 + (i % 2)) for i in range(3)])
        self.assertEqual(clinic.verdict(*clinic.source('r0')).status, 'UNDECIDED')
        clinic = GateClinic(min_entries=6, near=0.85, far=0.55)
        rows = [campaign((0, 0), i, 180 + i % 3) for i in range(14)]
        self.feed(clinic, rows)
        verdict = clinic.verdict(*clinic.source('r0'))
        self.assertEqual(verdict.status, 'ROOT', verdict)
        self.assertIn('root:far_from_pass', verdict.evidence)
        self.assertEqual(verdict.target_act, 1)

    def test_near_miss_and_retry_lift_give_depth(self):
        clinic = GateClinic(min_entries=6)
        rows = [campaign((0, 0), i, 340 + 8 * i) for i in range(7)]
        self.feed(clinic, rows)
        retry = campaign((0, 0), 0, 395)
        spec = {'kind': 'gate_retry', 'repair': {'level': 1, 'source': 'r0'},
                'request': {'history': retry['trace'][:7]}}
        clinic.observe(retry, 'retry', spec)
        verdict = clinic.verdict(*clinic.source('r0'))
        self.assertEqual(verdict.status, 'DEPTH', verdict)
        self.assertEqual(verdict.kind, 'TACTICAL')
        self.assertIn('depth:near_miss', verdict.evidence)

    def test_readiness_dead_is_root_and_viable_hp_sensitive_is_resource(self):
        clinic = GateClinic(min_entries=6)
        rows = [campaign((0, 0), i, 250 + (i % 3) * 5) for i in range(7)]
        self.feed(clinic, rows)
        for index in range(7):
            clinic.record_readiness('r%d' % index, 'DEAD_AT_FULL_HP', b'group-%d' % (index % 2))
        self.assertEqual(clinic.verdict(*clinic.source('r0')).status, 'ROOT')
        clinic = GateClinic(min_entries=6)
        rows = [campaign((0, 1), i, 150 + 25 * i, f2_hp=20 + 8 * i) for i in range(8)]
        self.feed(clinic, rows)
        clinic.record_readiness('r0', 'VIABLE', b'group')
        verdict = clinic.verdict(*clinic.source('r0'))
        self.assertEqual(verdict.status, 'DEPTH', verdict)
        self.assertEqual(verdict.kind, 'RESOURCE')

    def test_pass_synthetic_cache_and_contention(self):
        clinic = GateClinic(min_entries=6)
        self.feed(clinic, [campaign((0, 0), i, 100) for i in range(6)])
        self.assertTrue(clinic.contended())
        self.assertFalse(clinic.observe(dict(campaign(), synthetic=True), 'syn'))
        self.assertFalse(clinic.observe(campaign(), 'cache', {'cache_hit': True}))
        clinic.observe(campaign((0, 0), 20, passed=True), 'win')
        self.assertEqual(clinic.verdict(*clinic.source('r0')).status, 'PASSED')
        self.assertFalse(clinic.contended())
        json.dumps(clinic.snapshot())

    def test_root_siblings_move_the_reroot_target_one_act_back(self):
        clinic = GateClinic(min_entries=6, escalate_after=3)
        for sibling in range(3):
            rows = [campaign((0, sibling), i, 160 + i % 2) for i in range(14)]
            self.feed(clinic, rows, start=100 * sibling)
            self.assertEqual(clinic.verdict(*clinic.source('r%d' % (100 * sibling))).status, 'ROOT')
        rows = [campaign((0, 3), i, 160) for i in range(14)]
        self.feed(clinic, rows, start=900)
        self.assertEqual(clinic.target_act(*clinic.source('r900')), 0)


class ClinicFocusTests(unittest.TestCase):
    def scheduler(self, **options):
        clinic = GateClinic(min_entries=6, prior=StructuralPrior())
        return ClinicFocusScheduler(clinic=clinic, explore=0, **options)

    def test_root_stem_picks_reroot_earlier_act_and_depth_stays_local(self):
        s = self.scheduler(root_share=100)
        rows = [campaign((0, 0), i, 170 + i % 2) for i in range(14)]
        for index, result in enumerate(rows):
            s.add(result, 'r%d' % index)
        picks = []
        while len(picks) < 6:
            group = s.next()
            if group is None:
                break
            picks.append(group)
        self.assertGreaterEqual(len(picks), 3)
        self.assertTrue(all(p['clinic']['verdict'] == 'ROOT' for p in picks))
        self.assertEqual(picks[0]['clinic']['mode'], 'reroot')
        self.assertLess(picks[0]['act'], 2)
        d = self.scheduler(depth_share=0)
        rows = [campaign((1, 1), i, 340 + 8 * i, options=20) for i in range(7)]
        for index, result in enumerate(rows):
            d.add(result, 'd%d' % index)
        pick = d.next()
        self.assertEqual(pick['clinic']['mode'], 'local')
        self.assertEqual(pick['act'], 2)

    def test_proposals_are_exact_unique_legal_prefixes(self):
        s = self.scheduler()
        rows = [campaign((0, 0), i, 170) for i in range(14)]
        for index, result in enumerate(rows):
            s.add(result, 'r%d' % index)
        seen = set()
        for _ in range(25):
            group = s.next()
            if group is None:
                break
            key = canonical(group['prefix'])
            self.assertNotIn(key, seen)
            seen.add(key)
            source = next(r for i, r in enumerate(rows) if 'r%d' % i == group['source'])
            index = group['index']
            self.assertEqual(group['prefix'][:-1], source['trace'][:index])
            self.assertIn(group['prefix'][-1], source['decision_evidence'][index]['available_actions'])
        json.dumps(s.snapshot())

    def test_retry_rank_orders_by_verdict(self):
        s = self.scheduler()
        for index, result in enumerate([campaign((0, 0), i, 170) for i in range(14)]):
            s.add(result, 'r%d' % index)
        self.assertEqual(s.retry_rank({'repair': {'source': 'r0'}}), 2)
        self.assertEqual(s.retry_rank({'repair': {'source': 'unknown'}}), 1)
        queue = RepairQueue('gate')
        queue.append({'kind': 'gate_retry', 'priority': (0, 5), 'name': 'root'})
        queue.append({'kind': 'gate_retry', 'priority': (0, 1), 'name': 'depth'})
        ranks = {'root': 2, 'depth': 0}
        self.assertEqual(queue.popleft(rank=lambda item: ranks[item['name']])['name'], 'depth')


class RepairHintTests(unittest.TestCase):
    def test_reroot_carries_hint_toward_learned_deficit_repair(self):
        clinic = GateClinic(min_entries=6, prior=StructuralPrior())
        s = ClinicFocusScheduler(clinic=clinic, explore=0, root_share=100, repair_hints=True)
        rows = [campaign((0, 0), i, 170 + i % 2) for i in range(14)]
        # One trajectory learned that choosing C5_1 adds a scaling source.
        learned = campaign((1, 0), 0, 160)
        learned['decision_evidence'][1]['observation']['strategic'] = snapshot(scaling=1, strength=1)
        for index, result in enumerate(rows + [learned]):
            s.add(result, 'r%d' % index)
        self.assertGreater(s.effects.effect('card:C5_1')['scaling_total'], 0)
        group = s.next()
        self.assertEqual(group['clinic']['mode'], 'reroot')
        self.assertIn('card:C5_1', group.get('policy_hint') or {})
        self.assertTrue(all(1 <= tier <= 2 for row in group['policy_hint'].values() for tier in row))
        quiet = ClinicFocusScheduler(clinic=GateClinic(min_entries=6, prior=StructuralPrior()), explore=0, root_share=100)
        for index, result in enumerate(rows + [learned]):
            quiet.add(result, 'q%d' % index)
        self.assertNotIn('policy_hint', quiet.next())
        with self.assertRaises(ValueError):
            ClinicFocusScheduler(clinic=GateClinic(), repair_hints=True)

    def test_evaluator_merges_group_hint_only(self):
        from spire_exact.planning.search import Evaluator
        ev = object.__new__(Evaluator)
        ev.config = SearchConfig()
        ev.prior_provider = None
        plain = ev.tiers_for({'family': None})
        hinted = ev.tiers_for({'family': None, 'group': {'policy_hint': {'card:X': [1, 1, 1]}}})
        self.assertNotIn('card:X', plain)
        self.assertEqual(hinted['card:X'], [1, 1, 1])
        with self.assertRaises(ValueError):
            SearchConfig(scheduler='focus', clinic=True, clinic_hints=True)


class GovernorAndProfileTests(unittest.TestCase):
    def test_governor_caps_auxiliary_share_and_never_holds_core(self):
        state = {'contended': False}
        governor = AuxGovernor(True, quiet=10, contended=50, contended_fn=lambda: state['contended'])
        aux = {'kind': 'paired_card_probe'}
        self.assertFalse(governor.allow(aux))
        for _ in range(9):
            self.assertTrue(governor.allow({'kind': 'macro_focus'}))
            governor.record({'kind': 'macro_focus'})
        self.assertTrue(governor.allow(aux))
        governor.record(aux)
        self.assertFalse(governor.allow(aux))
        state['contended'] = True
        self.assertTrue(governor.allow(aux))
        for kind in ('deepen', 'gate_retry', 'restart', 'memory_prefix_followup', 'macro_focus'):
            self.assertNotIn(kind, AUX_KINDS)
        self.assertTrue(AuxGovernor(False).allow(aux))

    def args(self, profile, **values):
        names = ('clinic', 'clinic_hints', 'strategy_prior', 'paired_card_first', 'gate_timing', 'focus_stall', 'focus_stall_extended',
                 'focus_stall_f2', 'tail_mode', 'macro_plateau', 'macro_widening', 'macro_fair', 'macro_routes',
                 'gate_preset', 'scheduler', 'prior', 'repair_mode', 'normal_nodes', 'runtime_profile', 'worker_memory_mib',
                 'root_policies', 'focus_cluster_cap', 'final_gate_plan', 'root_async', 'requeue_lost',
                 'low_hp_routes_any_act', 'paired_card_every', 'paired_card_dedup', 'paired_card_merge',
                 'paired_card_joint', 'f1_winner_reuse', 'prefer_f1_hp', 'gold_shop_routes', 'low_hp_routes',
                 'lean_third_act', 'shop_preparation', 'resource_telemetry', 'memory_telemetry',
                 'preserve_completed_prefix', 'f2_readiness_probes', 'f2_dead_retry', 'f2_joint_focus', 'f2_joint_model')
        args = SimpleNamespace(feature_profile=profile, ascension=None, nodes=None, paired_card_probes=None,
                               **{name: None for name in names})
        for key, value in values.items():
            setattr(args, key, value)
        return resolve_entry_defaults(args)

    def test_final01_profile_values_and_old_profiles_unchanged(self):
        a = self.args(FINAL01_PROFILE)
        self.assertTrue(a.clinic)
        self.assertEqual((a.strategy_prior, a.paired_card_first, a.focus_stall, a.tail_mode, a.gate_preset),
                         ('builtin', 8, 0, 'off', 'escalate-evaluate'))
        self.assertEqual(self.args(FINAL01_PROFILE, gate_preset='escalate').gate_preset, 'escalate')
        self.assertTrue(a.macro_plateau and a.macro_fair and a.gate_timing and a.clinic_hints and a.f1_winner_reuse)
        self.assertFalse(any((a.f2_readiness_probes, a.f2_dead_retry, a.f2_joint_focus, a.f2_joint_model,
                              a.paired_card_probes, a.paired_card_joint)))
        for profile in ('i082', 'i085', 'legacy', 'i081', 'i075-final'):
            b = self.args(profile)
            self.assertEqual((b.clinic, b.strategy_prior, b.paired_card_first, b.gate_timing),
                             (False, 'off', 1, False), profile)
        self.assertEqual(self.args('i082').focus_stall, 32)
        self.assertTrue(self.args('i082').paired_card_probes and self.args('i082').paired_card_joint)
        self.assertTrue(self.args('legacy').paired_card_probes)
        self.assertEqual(self.args(FINAL01_PROFILE, focus_stall=5).focus_stall, 5)

    def test_argv_parses_and_config_validation(self):
        from spire_exact.planning.__main__ import parse_planner, search_config
        argv = ['--out', 'unused-dir', '--seed', '7', '--feature-profile', FINAL01_PROFILE, *i085_final01_argv()]
        _, a, gates, schedule, policies = parse_planner(argv)
        config = search_config(a, gates, schedule, policies)
        self.assertTrue(config.clinic)
        self.assertEqual(config.focus_stall, 0)
        self.assertEqual(len(config.gate_retry_plans), 2)
        with self.assertRaises(ValueError):
            SearchConfig(scheduler='focus', clinic=True, focus_stall=32)
        with self.assertRaises(ValueError):
            SearchConfig(scheduler='focus', strategy_prior='builtin')
        with self.assertRaises(ValueError):
            SearchConfig(scheduler='focus', clinic=True, tail_mode='shadow')
        self.assertFalse(SearchConfig().clinic)

    def test_paired_first_threshold(self):
        default = PairedCardProbes(True, 1, every=32)
        later = PairedCardProbes(True, 1, every=32, first=8)
        self.assertEqual(default.next_threshold, 1)
        self.assertEqual(later.next_threshold, 8)
        with self.assertRaises(ValueError):
            PairedCardProbes(True, 1, first=0)


class SimulatedLoopTests(unittest.TestCase):
    """The real planner loop on a synthetic world: wiring and determinism."""

    def test_final01_runs_and_reports_clinic_and_governor(self):
        from tools.sim_campaign import World, simulate
        summary = simulate(World(1001, 'easy'), 'final01', minutes=8)
        self.assertIn('stop_reason', summary)
        self.assertIsNotNone(summary['aux_governor'])
        self.assertTrue(summary['aux_governor']['enabled'])
        self.assertIsNotNone(summary['clinic_verdicts'])

    def test_simulation_is_deterministic(self):
        from tools.sim_campaign import World, simulate
        first = simulate(World(2003, 'root_act2'), 'i082', minutes=6)
        second = simulate(World(2003, 'root_act2'), 'i082', minutes=6)
        for key in ('verified', 'verified_seconds', 'evaluations', 'kinds', 'best_observation'):
            self.assertEqual(first[key], second[key], key)


if __name__ == '__main__':
    unittest.main()
