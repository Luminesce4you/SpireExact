"""i075 pure preparation fixtures; native replay and efficacy are separate.

No native process is needed for these cases; they don't establish native
fidelity, runtime performance or campaign efficacy.
"""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest
from spire_exact.canonical import canonical
from spire_exact.planning.preparation import NativeMap, PreparationCandidates, forge_menu_key
from spire_exact.planning.search import SearchConfig, research_consumer
from test_research_evaluator import fixture, progress_fixture, prefeature_request, CTX


def point(c, r):
    return {'col': c, 'row': r}


def graph_fixture():
    # The nearer shop has an elite on its shortest route, but a longer no-elite
    # path exists. A second shop is reachable only after the first.
    nodes = [(0, 1, 'Elite'), (1, 1, 'Monster'), (0, 2, 'Shop'),
             (1, 2, 'Unknown'), (2, 3, 'Shop'), (1, 3, 'RestSite'), (3, 5, 'Boss')]
    edges = [((0, 1), (0, 2)), ((1, 1), (1, 2)), ((1, 2), (0, 2)),
             ((0, 2), (2, 3)), ((1, 2), (1, 3)), ((1, 3), (3, 5)), ((2, 3), (3, 5))]
    return {'schema': 'spire-current-act-map/v1', 'act': 2, 'current_coord': point(3, 0),
            'nodes': [dict(point(c, r), type=t, can_modify=True) for c, r, t in nodes],
            'edges': [{'from': point(*a), 'to': point(*b)} for a, b in edges],
            'boss_coord': point(3, 5), 'second_boss_coord': None,
            'starting_coord': point(3, 0), 'start_coords': [point(0, 1), point(1, 1)],
            'width': 7, 'height': 5}


def source_fixture(gold='400', hp='40', maximum='100', act=2):
    actions = [dict(point(0, 1), kind='map'), dict(point(1, 1), kind='map')]
    obs = {'act': act, 'floor': 34, 'gold': gold, 'hp': hp, 'max_hp': maximum}
    template = {k: CTX[k] for k in ('seed', 'character', 'ascension', 'unlocks')}
    template.update(history=[], generate_candidate=True, capture_checkpoints=True,
                    advisor={'binary_identity': {'fixture': 'fixed'}, 'nodes': 17000},
                    policy_seed=71, policy_prior={'card:SOURCE': [0, 0, 2]}, research_progress=progress_fixture())
    source = {'schema': 'spire-map-decision-source/v1', 'phase': 'map', 'index': 0, 'entry_history': [],
              'context': PreparationCandidates.context(template), 'native_identity': progress_fixture()['native_identity'],
              'baseline_sha256': progress_fixture()['baseline_sha256'],
              'entry_observation': obs, 'available_actions': actions, 'graph': graph_fixture(),
              'source_native_state': {'run': {'native_json': '{"native_state":1}'}, 'progress': {}, 'action_runtime': {}}}
    source['graph']['act'] = act
    result = {'trace': [actions[0]], 'decision_evidence': [{'phase': 'map', 'observation': obs, 'available_actions': actions}],
              'map_decision_sources': [source]}
    return source, result, template


def forge_fixture(hp='60', maximum='100', prefix_col=0, metadata=True):
    _, _, template = source_fixture()
    chosen_map = {'kind': 'map', 'col': prefix_col, 'row': 3}
    rests = [{'kind': 'rest', 'index': 0, 'option': 'HEAL'}, {'kind': 'rest', 'index': 1, 'option': 'SMITH'}]
    obs = {'act': 2, 'floor': 46, 'hp': hp, 'max_hp': maximum,
           'deck': [{'id': 'STRIKE_IRONCLAD', 'upgrade': 0}, {'id': 'DEFEND_IRONCLAD', 'upgrade': 1},
                    {'id': 'STRIKE_IRONCLAD', 'upgrade': 0}],
           'relics': ['BURNING_BLOOD', 'STRIKE_DUMMY'], 'potions': [None, 'BLOOD_POTION']}
    result = {'trace': [chosen_map, rests[0]], 'decision_evidence': [
        {'phase': 'map', 'observation': {'act': 2, 'floor': 45}, 'available_actions': [chosen_map]},
        {'phase': 'rest', 'observation': obs, 'available_actions': rests}]}
    if metadata:
        result['preparation_menu_sources'] = [{'index': 1, 'phase': 'rest',
            'map_coord': point(1, 3), 'observation': deepcopy(obs)}]
    return result, template


class MapProposalTests(unittest.TestCase):
    def test_shop_route_uses_actual_graph_and_avoids_elite_if_possible(self):
        source, result, template = source_fixture(gold='200')
        planner = PreparationCandidates(SearchConfig(gold_shop_routes=True))
        rows = planner.observe(result, 'source', template, template, family='native')
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['kind'], 'macro_shop_route')
        self.assertEqual([(p['col'], p['row']) for p in row['request']['map_route_plan']['moves']], [(1, 1), (1, 2), (0, 2)])
        self.assertEqual(row['request']['policy_seed'], 71)
        self.assertEqual(row['request']['policy_prior'], template['policy_prior'])
        self.assertEqual(row['request']['advisor'], template['advisor'])
        self.assertEqual(row['request']['map_route_plan']['source'], source)
        self.assertTrue(research_consumer(row))
        self.assertFalse(row['request']['capture_checkpoints'])
        self.assertEqual(row['request']['map_route_plan']['shop_policy'], 'removal-only/v1')

    def test_two_shops_require_twice_threshold_and_connected_edges(self):
        _, result, template = source_fixture()
        rows = PreparationCandidates(SearchConfig(gold_shop_routes=True)).observe(result, 'x', template, template)
        self.assertEqual([len(row['request']['map_route_plan']['target_shops']) for row in rows], [1, 2])
        self.assertEqual(rows[1]['request']['map_route_plan']['moves'][-1], {'act': 2, 'col': 2, 'row': 3})
        source = result['map_decision_sources'][0]
        source['graph']['edges'] = [e for e in source['graph']['edges'] if e['to'] != point(2, 3)]
        rows = PreparationCandidates(SearchConfig(gold_shop_routes=True)).observe(result, 'y', template, template)
        self.assertEqual(len(rows), 1)

    def test_unknown_rooms_do_not_count_as_shops(self):
        source, result, template = source_fixture()
        for node in source['graph']['nodes']:
            if node['type'] == 'Shop':
                node['type'] = 'Unknown'
        planner = PreparationCandidates(SearchConfig(gold_shop_routes=True))
        self.assertEqual(planner.observe(result, 'x', template, template), [])

    def test_low_hp_route_covers_first_boss_and_strict_threshold(self):
        _, result, template = source_fixture(hp='49')
        planner = PreparationCandidates(SearchConfig(low_hp_routes=True))
        rows = planner.observe(result, 'x', template, template)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['kind'], 'macro_low_hp_route')
        self.assertEqual(rows[0]['request']['map_route_plan']['moves'][-1], {'act': 2, 'col': 3, 'row': 5})
        self.assertEqual([(p['col'], p['row']) for p in rows[0]['request']['map_route_plan']['moves']], [(1, 1), (1, 2), (1, 3), (3, 5)])
        _, result, template = source_fixture(hp='50')
        self.assertEqual(planner.observe(result, 'equal', template, template), [])
        _, result, template = source_fixture(hp='unknown')
        self.assertEqual(planner.observe(result, 'missing', template, template), [])

    def test_any_act_modifier_keeps_threshold_and_targets_each_acts_first_boss(self):
        for act in (0, 1, 2):
            for hp, expected in (('49', 1), ('50', 0), ('0', 0), ('unknown', 0)):
                with self.subTest(act=act, hp=hp):
                    _, result, template = source_fixture(hp=hp, act=act)
                    cfg = SearchConfig(low_hp_routes=True, low_hp_routes_any_act=True)
                    rows = PreparationCandidates(cfg).observe(result, 'any', template, template)
                    self.assertEqual(len(rows), expected)
                    if rows:
                        self.assertEqual(rows[0]['kind'], 'macro_low_hp_route')
                        plan = rows[0]['request']['map_route_plan']
                        self.assertTrue(all(move['act'] == act for move in plan['moves']))
                        self.assertEqual(plan['moves'][-1], {'act': act, 'col': 3, 'row': 5})
                        self.assertEqual(plan['target_shops'], [])
                        self.assertNotIn('shop_policy', plan)

    def test_any_act_off_preserves_third_act_request_bytes_and_skips_earlier_acts(self):
        for act in (0, 1, 2):
            with self.subTest(act=act):
                _, result, template = source_fixture(hp='49', act=act)
                old = SimpleNamespace(**{key: value for key, value in SearchConfig(low_hp_routes=True).__dict__.items()
                                         if key != 'low_hp_routes_any_act'})
                # Exercise the pre-field config interface, not another copy of
                # the new default. No any-act field was present on old configs.
                rows_old = PreparationCandidates(old).observe(result, 'legacy', template, template)
                rows_off = PreparationCandidates(SearchConfig(low_hp_routes=True, low_hp_routes_any_act=False)).observe(
                    result, 'off', template, template)
                self.assertEqual([canonical(row['request']) for row in rows_old],
                                 [canonical(row['request']) for row in rows_off])
                self.assertEqual(len(rows_off), int(act == 2))
        _, result, template = source_fixture(hp='49', act=0)
        self.assertEqual(PreparationCandidates(SearchConfig(low_hp_routes_any_act=True)).observe(
            result, 'modifier-alone', template, template), [])

    def test_low_hp_once_per_act_and_per_trajectory_limit_remain(self):
        source, result, template = source_fixture(hp='49', act=0)
        second = deepcopy(source); second.update(index=1, entry_history=deepcopy(result['trace']))
        result['trace'].append(deepcopy(result['trace'][0]))
        result['decision_evidence'].append(deepcopy(result['decision_evidence'][0]))
        result['map_decision_sources'].append(second)
        planner = PreparationCandidates(SearchConfig(low_hp_routes=True, low_hp_routes_any_act=True))
        self.assertEqual(len(planner.observe(result, 'one-per-act', template, template)), 1)
        _, result, template = source_fixture(gold='400', hp='49', act=1)
        rows = PreparationCandidates(SearchConfig(gold_shop_routes=True, low_hp_routes=True,
            low_hp_routes_any_act=True, preparation_limit=1)).observe(result, 'bounded', template, template)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['kind'], 'macro_shop_route')

    def test_shop_routes_already_work_in_first_and_second_act(self):
        for act in (0, 1, 2):
            with self.subTest(act=act):
                _, result, template = source_fixture(gold='400', act=act)
                rows = PreparationCandidates(SearchConfig(gold_shop_routes=True)).observe(result, 'shop', template, template)
                self.assertEqual([len(row['request']['map_route_plan']['target_shops']) for row in rows], [1, 2])
                self.assertTrue(all(target['act'] == act for row in rows
                                    for target in row['request']['map_route_plan']['target_shops']))

    def test_target_shop_removal_is_preserved_when_additional_preparation_is_off(self):
        _, result, template = source_fixture(gold='200')
        cfg = SearchConfig(gold_shop_routes=True, shop_preparation=True)
        rows = PreparationCandidates(cfg).observe(result, 'x', template, template)
        self.assertEqual(rows[0]['request']['map_route_plan']['shop_policy'], 'potions_remove_relic/v1')
        rows = PreparationCandidates(SearchConfig(gold_shop_routes=True, shop_preparation=False)).observe(
            result, 'removal-retained', template, template)
        self.assertEqual(rows[0]['request']['map_route_plan']['shop_policy'], 'removal-only/v1')
        inactive = PreparationCandidates(SearchConfig(shop_preparation=True))
        self.assertEqual(inactive.observe(result, 'no-target', template, template), [])

    def test_rejections_are_missing_sources_not_no_good_cuts(self):
        mutations = [lambda s: s.update(index=2), lambda s: s['context'].update(seed='different'),
                     lambda s: s.update(native_identity={'wrong': 1}), lambda s: s['source_native_state'].pop('progress'),
                     lambda s: s['available_actions'].append({'kind': 'map', 'col': 6, 'row': 1})]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                source, result, template = source_fixture()
                mutate(source)
                planner = PreparationCandidates(SearchConfig(gold_shop_routes=True))
                self.assertEqual(planner.observe(result, 'x', template, template), [])
                self.assertTrue(planner.rejections)
                self.assertNotIn('exclusions', planner.snapshot())

    def test_budget_deduplication_and_no_recursive_route_requests(self):
        _, result, template = source_fixture()
        planner = PreparationCandidates(SearchConfig(gold_shop_routes=True, low_hp_routes=True, preparation_limit=1))
        rows = planner.observe(result, 'x', template, template)
        self.assertEqual(len(rows), 1)
        self.assertEqual(planner.observe(result, 'x', template, template), [])
        self.assertEqual(planner.observe(result, 'consumer', template, rows[0]['request']), [])
        self.assertEqual(planner.snapshot()['counts']['proposals'], 1)

    def test_positive_hop_candidate_path_terminates_on_cyclic_graph(self):
        source, _, _ = source_fixture()
        source['graph']['edges'].append({'from': point(1, 2), 'to': point(1, 1)})
        graph = NativeMap(source['graph'], source['available_actions'])
        self.assertEqual(graph.path((3, 5)), [(1, 1), (1, 2), (1, 3), (3, 5)])

    def test_lean_menu_generates_only_actual_smith_and_never_card_skip(self):
        _, _, template = source_fixture()
        actions = [{'kind': 'card_reward', 'index': 0}, {'kind': 'card_skip'}]
        rests = [{'kind': 'rest', 'index': 0, 'option': 'HEAL'}, {'kind': 'rest', 'index': 1, 'option': 'SMITH'}]
        result = {'trace': [actions[0], rests[0]], 'decision_evidence': [
            {'phase': 'card_reward', 'observation': {'act': 2}, 'available_actions': actions},
            {'phase': 'rest', 'observation': {'act': 2, 'hp': '60', 'max_hp': '100'}, 'available_actions': rests}]}
        rows = PreparationCandidates(SearchConfig(lean_third_act=True)).observe(result, 'x', template, template)
        self.assertEqual([row['kind'] for row in rows], ['macro_forge'])
        result['decision_evidence'][1]['observation']['hp'] = '49'
        rows = PreparationCandidates(SearchConfig(lean_third_act=True)).observe(result, 'y', template, template)
        self.assertEqual(rows, [])


class ForgeMenuGroupingTests(unittest.TestCase):
    def test_exact_and_threshold_differ_only_in_current_hp_projection(self):
        result, _ = forge_fixture()
        evidence = result['decision_evidence'][1]
        coordinate = point(1, 3)
        exact = forge_menu_key(evidence, map_coord=coordinate)
        grouped = forge_menu_key(evidence, 'threshold', map_coord=coordinate)
        changed = deepcopy(evidence); changed['observation']['hp'] = '80'
        self.assertNotEqual(exact, forge_menu_key(changed, map_coord=coordinate))
        self.assertEqual(grouped, forge_menu_key(changed, 'threshold', map_coord=coordinate))
        changed['observation']['max_hp'] = '120'
        self.assertNotEqual(grouped, forge_menu_key(changed, 'threshold', map_coord=coordinate))
        changed['observation'].update(hp='49', max_hp='100')
        self.assertNotEqual(grouped, forge_menu_key(changed, 'threshold', map_coord=coordinate))
        changed['observation']['hp'] = '50.0000'
        self.assertEqual(grouped, forge_menu_key(changed, 'threshold', map_coord=coordinate))
        changed['observation']['hp'] = '60.0'
        self.assertEqual(exact, forge_menu_key(changed, map_coord=coordinate))

    def test_card_multiset_preserves_counts_and_upgrades_potions_keep_slot_order(self):
        result, _ = forge_fixture(); evidence = result['decision_evidence'][1]
        key = forge_menu_key(evidence, map_coord=point(1, 3))
        changed = deepcopy(evidence)
        changed['observation']['deck'] = changed['observation']['deck'][1:] + changed['observation']['deck'][:1]
        self.assertEqual(key, forge_menu_key(changed, map_coord=point(1, 3)))
        changed['observation']['deck'].pop()
        self.assertNotEqual(key, forge_menu_key(changed, map_coord=point(1, 3)))
        for mutate in (lambda obs: obs['deck'][0].update(upgrade=1),
                       lambda obs: obs['relics'].reverse(), lambda obs: obs['potions'].reverse(),
                       lambda obs: obs.update(floor=47), lambda obs: obs.update(act=1)):
            changed = deepcopy(evidence); mutate(changed['observation'])
            self.assertNotEqual(key, forge_menu_key(changed, map_coord=point(1, 3)))
        changed = deepcopy(evidence); changed['phase'] = 'card_reward'
        self.assertNotEqual(key, forge_menu_key(changed, map_coord=point(1, 3)))

    def test_missing_invalid_or_conflicting_fields_never_make_a_shared_unknown_key(self):
        result, _ = forge_fixture(); evidence = result['decision_evidence'][1]
        self.assertIsNone(forge_menu_key(evidence))
        missing_phase = deepcopy(evidence); missing_phase.pop('phase')
        self.assertIsNone(forge_menu_key(missing_phase, map_coord=point(1, 3)))
        for field in ('act', 'floor', 'deck', 'relics', 'potions', 'hp', 'max_hp'):
            changed = deepcopy(evidence); changed['observation'].pop(field)
            self.assertIsNone(forge_menu_key(changed, map_coord=point(1, 3)))
        for mutate in (lambda obs: obs.update(act=True), lambda obs: obs.update(max_hp='0'),
                       lambda obs: obs.update(hp='NaN'), lambda obs: obs['deck'][0].update(upgrade=True),
                       lambda obs: obs.update(potions=['']), lambda obs: obs.update(current_coord=point(2, 3))):
            changed = deepcopy(evidence); mutate(changed['observation'])
            self.assertIsNone(forge_menu_key(changed, map_coord=point(1, 3)))
        self.assertIsNone(forge_menu_key(evidence, map_coord={'col': True, 'row': 3}))
        changed = deepcopy(evidence); changed['observation'].update(deck=[], relics=[], potions=[])
        self.assertIsNotNone(forge_menu_key(changed, map_coord=point(1, 3)))

    def test_same_native_menu_under_different_prefixes_only_proposes_forge_once(self):
        planner = PreparationCandidates(SearchConfig(lean_third_act=True))
        first, template = forge_fixture(prefix_col=0)
        second, _ = forge_fixture(prefix_col=2)
        before = canonical(second)
        self.assertEqual(len(planner.observe(first, 'first', template, template)), 1)
        self.assertEqual(planner.observe(second, 'second', template, template), [])
        self.assertEqual(planner.snapshot()['counts']['forge_menu_duplicates'], 1)
        self.assertEqual(canonical(second), before)  # Original search branch/evidence intact.
        self.assertNotIn('exclusions', planner.snapshot())

    def test_threshold_is_optional_exact_is_default_and_hp_changes_propose_again(self):
        for mode, expected in (('exact', 1), ('threshold', 0)):
            with self.subTest(mode=mode):
                planner = PreparationCandidates(SearchConfig(lean_third_act=True, forge_menu_dedup=mode))
                first, template = forge_fixture(hp='60', prefix_col=0)
                second, _ = forge_fixture(hp='80', prefix_col=2)
                self.assertEqual(len(planner.observe(first, 'first', template, template)), 1)
                self.assertEqual(len(planner.observe(second, 'second', template, template)), expected)
        self.assertEqual(PreparationCandidates(SearchConfig()).snapshot()['forge_menu_dedup'], 'exact')

    def test_legacy_and_misbound_side_tables_keep_proposals_and_report_missing_key(self):
        for defect in ('legacy', 'index', 'phase', 'observation', 'duplicate'):
            with self.subTest(defect=defect):
                planner = PreparationCandidates(SearchConfig(lean_third_act=True))
                first, template = forge_fixture(prefix_col=0, metadata=False)
                second, _ = forge_fixture(prefix_col=2, metadata=defect != 'legacy')
                if defect != 'legacy':
                    row = second['preparation_menu_sources'][0]
                    if defect == 'index': row['index'] = 0
                    elif defect == 'phase': row['phase'] = 'map'
                    elif defect == 'observation': row['observation']['hp'] = '61'
                    elif defect == 'duplicate': second['preparation_menu_sources'].append(deepcopy(row))
                self.assertEqual(len(planner.observe(first, 'first', template, template)), 1)
                self.assertEqual(len(planner.observe(second, 'second', template, template)), 1)
                self.assertEqual(planner.snapshot()['counts']['forge_menu_key_missing'], 2)
        result, template = forge_fixture(metadata=False)
        result['decision_evidence'][1]['observation']['current_coord'] = point(1, 3)
        planner = PreparationCandidates(SearchConfig(lean_third_act=True))
        self.assertEqual(len(planner.observe(result, 'explicit', template, template)), 1)
        self.assertNotIn('forge_menu_key_missing', planner.snapshot()['counts'])

    def test_forge_still_requires_third_act_actual_smith_and_at_least_half_hp(self):
        for act, hp, smith, expected in ((2, '50', True, 1), (2, '49', True, 0),
                                         (0, '60', True, 0), (1, '60', True, 0), (2, '60', False, 0)):
            with self.subTest(act=act, hp=hp, smith=smith):
                result, template = forge_fixture(hp=hp)
                result['decision_evidence'][1]['observation']['act'] = act
                if not smith:
                    result['decision_evidence'][1]['available_actions'] = result['decision_evidence'][1]['available_actions'][:1]
                rows = PreparationCandidates(SearchConfig(lean_third_act=True)).observe(result, 'x', template, template)
                self.assertEqual(len(rows), expected)

    def test_already_chosen_smith_does_not_mark_menu_as_used_without_new_proposal(self):
        result, template = forge_fixture()
        result['trace'][1] = result['decision_evidence'][1]['available_actions'][1]
        planner = PreparationCandidates(SearchConfig(lean_third_act=True))
        self.assertEqual(planner.observe(result, 'already-smith', template, template), [])
        alternative, _ = forge_fixture(prefix_col=2)
        self.assertEqual(len(planner.observe(alternative, 'needs-smith', template, template)), 1)


class PreparationDispatchTests(unittest.TestCase):
    def test_new_switches_off_do_not_export_baseline_or_change_old_request(self):
        with TemporaryDirectory() as directory:
            ev, pool = fixture(directory, gold_shop_routes=False, low_hp_routes=False,
                               lean_third_act=False, shop_preparation=False, resource_telemetry=False)
            self.assertEqual(canonical(ev.request([])), canonical(prefeature_request([])))
            self.assertEqual(pool.baseline_calls, [])

    def test_map_consumer_uses_disposable_and_bypasses_cp_cache_and_prior(self):
        with TemporaryDirectory() as directory, patch('spire_exact.planning.search.runtime_snapshot', return_value={}):
            ev, pool = fixture(directory, gold_shop_routes=True)
            ev.prior_provider = Mock(return_value={'wrong': [3]})
            request = ev.request([])
            request.update(map_route_plan={'fixture': 'native-will-check'}, checkpoint='unsafe', policy_seed=912)
            ev.dispatch([{'kind': 'macro_shop_route', 'request': request}])
            ev.collect()
            sent, options = pool.calls[0]
            self.assertEqual(options, {'fresh': True, 'disposable': True})
            self.assertFalse(sent['capture_checkpoints'])
            self.assertNotIn('checkpoint', sent)
            self.assertFalse(ev.cache.get.called)
            self.assertFalse(ev.cache.put.called)
            self.assertFalse(ev.checkpoints.nearest.called)
            self.assertFalse(ev.checkpoints.import_result.called)
            self.assertFalse(ev.prior_provider.called)
            self.assertEqual(sent['policy_seed'], 912)
            self.assertEqual(ev.records[0]['research_work']['role'], 'consumer_preparation')

    def test_telemetry_has_its_own_flag_and_no_route_proposal(self):
        with TemporaryDirectory() as directory:
            ev, pool = fixture(directory, resource_telemetry=True)
            self.assertTrue(ev.request([])['capture_resource_telemetry'])
            self.assertNotIn('capture_route_graph', ev.request([]))
            self.assertEqual(len(pool.baseline_calls), 1)
            _, result, template = source_fixture()
            self.assertEqual(PreparationCandidates(ev.config).observe(result, 'x', template, template), [])

    def test_invalid_knobs_rejected_without_native_work(self):
        for knobs in ({'gold_shop_threshold': 0}, {'low_hp_percent': 101}, {'preparation_limit': 0},
                      {'low_hp_routes': 1}, {'resource_telemetry': 'yes'},
                      {'low_hp_routes_any_act': 1}, {'forge_menu_dedup': 'unknown'}):
            with self.subTest(knobs=knobs), self.assertRaises(ValueError):
                SearchConfig(**knobs)


if __name__ == '__main__':
    unittest.main()
