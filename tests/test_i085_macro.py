"""No-DLL i085 tests. Toy worlds are NOT native efficacy measurements."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import argparse
import unittest

from spire_exact.canonical import canonical
from spire_exact.planning.final_defaults import (resolve_entry_defaults, FINAL_FEATURES,
    I081_FEATURES, I082_PLANNER, I085_PLANNER)
from spire_exact.planning.focus import FocusScheduler
from spire_exact.planning.indexed_strategy import IndexedStrategicScheduler
from spire_exact.planning.macro_i085 import (MacroService, MacroRoutePortfolio, enumerate_routes,
    representative_routes, route_distance)
from spire_exact.planning.preparation import PreparationCandidates, NativeMap
from spire_exact.planning.search import SearchConfig, research_consumer
from tools.run_release_source import settings, effective_parameters
from test_focus import trajectory
from test_preparation import source_fixture
from test_focus_search import run


class PlateauTests(unittest.TestCase):
    def test_equal_quality_different_macro_neighborhood_is_retained(self):
        old, new = FocusScheduler(), FocusScheduler(plateau_diversity=True)
        a = trajectory(floors=3, tag=1, deck=['A'])
        b = trajectory(floors=3, tag=2, deck=['B'])
        for scheduler in (old, new):
            scheduler.add(a,'a'); scheduler.add(b,'b')
        self.assertEqual(set(old.sources), {'a'})
        self.assertEqual(set(new.sources), {'a','b'})
        self.assertEqual(new.snapshot()['macro_plateaus']['equal_quality_admissions'],1)

    def test_identical_neighborhood_does_not_consume_second_focus_place(self):
        s=FocusScheduler(plateau_diversity=True)
        a=trajectory(floors=3)
        s.add(a,'a');s.add(deepcopy(a),'again')
        self.assertEqual(len(s.sources),1)
        self.assertEqual(s.neighborhood_duplicates,1)

    def test_stronger_outcome_with_same_neighborhood_replaces_old_source(self):
        s=FocusScheduler(elites=1,pool=2,plateau_diversity=True)
        a=trajectory(floors=3,death_hp=80);b=trajectory(floors=3,death_hp=10)
        s.add(a,'weak');s.add(b,'strong')
        self.assertEqual(set(s.sources),{'strong'})
        self.assertEqual(s.neighborhood_improvements,1)
        s.add(a,'weak-again');self.assertEqual(set(s.sources),{'strong'})

    def test_pool_bounds_and_retention_keys_survive_evictions(self):
        s=FocusScheduler(elites=2,pool=4,cluster_cap=2,plateau_diversity=True)
        for n in range(18):
            s.add(trajectory(floors=3+n%2,tag=n,deck=[str(n)]),'s'+str(n))
            self.assertLessEqual(len(s.sources),4)
            self.assertEqual(s.retained_neighborhoods,{x.retention_key for x in s.sources.values()})
            self.assertEqual(s.qualities,{x.quality for x in s.sources.values()})

    def test_old_flag_off_preserves_group_sequence(self):
        a,b=FocusScheduler(),FocusScheduler(plateau_diversity=False,recover_deferred=False)
        for n in range(5):
            r=trajectory(floors=4,tag=n,death_hp=50-n)
            a.add(r,str(n));b.add(deepcopy(r),str(n))
        for _ in range(30):self.assertEqual(a.next(),b.next())


class WideningTests(unittest.TestCase):
    @staticmethod
    def menu(n=31):
        actions=[{'kind':'card_reward','index':i} for i in range(n)]
        return {'trace':[actions[0]], 'decision_evidence':[{'phase':'card_reward',
            'observation':{'act':0,'floor':1},'available_actions':actions}]}

    def test_wide_menu_eventually_enumerates_every_exact_alternative(self):
        s=IndexedStrategicScheduler(site_cap=3,recover_deferred=True)
        s.add(self.menu(),'a')
        self.assertEqual(len(s.branches),3)
        self.assertEqual(len(s.deferred_branches),27)
        rows=[]
        while (g:=s.next()) is not None:rows.append(g)
        self.assertEqual({g['prefix'][-1]['index'] for g in rows},set(range(1,31)))
        self.assertEqual(len(rows),30)
        self.assertEqual(s.snapshot()['deferred_pending'],0)

    def test_baseline_reproduction_has_no_recovery_queue(self):
        s=IndexedStrategicScheduler(site_cap=3)
        s.add(self.menu(),'a')
        rows=[]
        while (g:=s.next()) is not None:rows.append(g)
        self.assertEqual(len(rows),3)
        self.assertNotIn('recover_deferred',s.snapshot())

    def test_focus_take_and_executed_prefix_remove_deferred_work(self):
        s=IndexedStrategicScheduler(site_cap=2,recover_deferred=True)
        s.add(self.menu(12),'a')
        chosen=list(s.deferred_branches)[:2]
        s.take(chosen[0])
        r=self.menu(12);r['trace']=s.prefixes.restore(chosen[1])
        s.add(r,'executed')
        self.assertNotIn(chosen[0],s.deferred_branches)
        self.assertNotIn(chosen[1],s.deferred_branches)
        seen=[]
        while (g:=s.next()) is not None:seen.append(canonical(g['prefix']))
        self.assertNotIn(canonical(s.prefixes.restore(chosen[0])),seen)
        self.assertNotIn(canonical(s.prefixes.restore(chosen[1])),seen)
        self.assertEqual(len(seen),len(set(seen)))

    def test_allocation_never_merges_two_exact_parent_prefixes(self):
        s=IndexedStrategicScheduler(site_cap=1,recover_deferred=True)
        for tag in (1,2):
            r=self.menu(5);r['trace'].insert(0,{'kind':'event','tag':tag})
            r['decision_evidence'].insert(0,{'phase':'event','observation':{},'available_actions':[]})
            s.add(r,str(tag))
        rows=[]
        while (g:=s.next()) is not None:rows.append(canonical(g['prefix']))
        self.assertEqual(len(rows),8)
        self.assertEqual(len(set(rows)),8)

    def test_i085_sources_can_use_deferred_options_without_double_submission(self):
        s=FocusScheduler(site_cap=2,plateau_diversity=True,recover_deferred=True)
        s.add(trajectory(floors=2,options=25),'a')
        keys=[]
        for _ in range(200):
            group=s.next()
            if group is None:break
            keys.append(canonical(group['prefix']))
        self.assertEqual(len(keys),len(set(keys)))
        self.assertEqual(len(keys),96)


class RouteTests(unittest.TestCase):
    def make(self,mode='on',act=0,**kw):
        source,result,template=source_fixture(act=act)
        cfg=SearchConfig(macro_routes=mode,**kw)
        p=PreparationCandidates(cfg)
        return MacroRoutePortfolio(cfg,p),source,result,template

    def test_paths_use_actual_edges_and_terminate_at_first_boss(self):
        p,source,result,t=self.make()
        graph=NativeMap(source['graph'],source['available_actions'])
        paths,audit=enumerate_routes(graph,max_paths=256,max_expansions=4096)
        self.assertGreaterEqual(len(paths),3)
        self.assertFalse(audit['enumeration_truncated'])
        for path in paths:
            self.assertIn(path[0],graph.starts);self.assertEqual(path[-1],graph.boss)
            self.assertEqual(len(path),len(set(path)))
            self.assertTrue(all(b in graph.edges[a] for a,b in zip(path,path[1:])))
        picks=representative_routes(graph,paths,limit=3)
        self.assertLessEqual(len(picks),3)
        for i,path in enumerate(picks):
            for other in picks[:i]:self.assertGreater(route_distance(graph,path,other),0)

    def test_cyclic_graph_and_tiny_budget_stay_bounded_unresolved(self):
        p,s,r,t=self.make()
        g=NativeMap(s['graph'],s['available_actions'])
        g.edges[(1,2)].add((1,1))
        paths,audit=enumerate_routes(g,max_paths=256,max_expansions=1)
        self.assertEqual(audit['expanded_paths'],1)
        self.assertTrue(audit['enumeration_truncated'])
        self.assertEqual(paths,[])
        paths,_=enumerate_routes(g,max_paths=256,max_expansions=4096)
        self.assertTrue(all(len(x)==len(set(x)) for x in paths))

    def test_legal_first_moves_not_graph_start_metadata_define_entry(self):
        p,s,r,t=self.make()
        s['graph']['start_coords']=[]
        g=NativeMap(s['graph'],s['available_actions'])
        paths,_=enumerate_routes(g,max_paths=256,max_expansions=4096)
        self.assertEqual({p[0] for p in paths},set(g.starts))

    def test_shadow_is_nonmutating_and_has_no_native_dispatch(self):
        p,s,r,t=self.make('shadow')
        saved=deepcopy((r,t))
        p.observe(r,'s',t,t)
        self.assertEqual((r,t),saved)
        self.assertGreater(p.counts['candidates'],0)
        self.assertEqual(p.snapshot()['pending_candidates'],0)
        self.assertIsNone(p.next(SimpleNamespace(),1000))

    def test_actual_proposals_inherit_full_contract_no_shop_override(self):
        p,s,r,t=self.make()
        p.observe(r,'s',t,t,family='native')
        self.assertIsNone(p.next(SimpleNamespace(),7))
        spec=p.next(SimpleNamespace(),8)
        self.assertIsNotNone(spec);self.assertTrue(research_consumer(spec))
        req=spec['request'];plan=req['map_route_plan']
        self.assertEqual(plan['source'],s)
        self.assertEqual(plan['target_shops'],[])
        self.assertNotIn('shop_policy',plan)
        self.assertFalse(req['capture_checkpoints'])
        self.assertEqual(req['advisor'],t['advisor'])
        self.assertEqual(req['policy_prior'],t['policy_prior'])
        self.assertEqual(req['policy_seed'],t['policy_seed'])
        self.assertEqual(req['research_progress'],t['research_progress'])
        self.assertNotIn('checkpoint',req)
        self.assertIsNone(p.next(SimpleNamespace(),15))

    def test_blocked_cohorts_remain_pending_and_resume(self):
        p,s,r,t=self.make();p.observe(r,'s',t,t)
        n=p.snapshot()['pending_candidates']
        blocked=SimpleNamespace(dispatch_blocked=lambda _:True)
        self.assertIsNone(p.next(blocked,100))
        self.assertEqual(p.snapshot()['pending_candidates'],n)
        self.assertIsNotNone(p.next(SimpleNamespace(),100))

    def test_synthetic_bad_identity_and_recursive_sources_do_not_enter(self):
        for mutation,request_change in ((lambda r:r.update(synthetic=True),None),
            (lambda r:r['map_decision_sources'][0].update(native_identity={}),None),
            (lambda r:None,'map_route_plan')):
            p,s,r,t=self.make();mutation(r);req=deepcopy(t)
            if request_change:req[request_change]={}
            p.observe(r,'s',t,req)
            self.assertEqual(p.snapshot()['pending_candidates'],0)

    def test_act3_stays_out_of_new_portfolio(self):
        p,s,r,t=self.make(act=2);p.observe(r,'s',t,t)
        self.assertEqual(p.counts['candidates'],0)

    def test_exact_duplicate_and_distinct_full_source_states(self):
        p,s,r,t=self.make();p.observe(r,'s',t,t);n=p.counts['sources']
        p.observe(deepcopy(r),'same',t,t)
        self.assertEqual(p.counts['sources'],n)
        s['source_native_state']['run']['native_json']='{"different_counter":1}'
        p.observe(r,'different-native-state',t,t)
        self.assertEqual(p.counts['sources'],n+1)

    def test_queue_memory_cap_is_explicit_and_drops_no_ordinary_branches(self):
        p,s,r,t=self.make(macro_route_queue_mib=1)
        for i in range(20):
            s['source_native_state']['run']['native_json']=str(i)+'X'*80000
            p.observe(r,str(i),t,t)
            self.assertLessEqual(p.pending_bytes,p.budget)
            self.assertLessEqual(p.seen_bytes,p.budget)
        self.assertGreater(p.counts['queued_candidates_evicted_unresolved'],0)

    def test_request_contract_mismatch_is_rejected(self):
        p,s,r,t=self.make();wrong=deepcopy(t);wrong['seed']='not-source'
        p.observe(r,'s',t,wrong)
        self.assertEqual(p.snapshot()['pending_candidates'],0)
        self.assertEqual(p.counts['source_request_contract_rejections'],1)


class ServiceAndIntegrationTests(unittest.TestCase):
    def test_auxiliary_debt_clears_only_on_actual_core_proposal(self):
        s=MacroService(True,4)
        for _ in range(4):s.record('synthetic_or_preparation')
        self.assertTrue(s.due())
        s.record('macro_focus',core=True,forced=True)
        self.assertFalse(s.due());self.assertEqual(s.forced,1)

    def test_real_solve_loop_cannot_be_flooded_by_preparation(self):
        def flood(self,result,label,template,source_request,**kw):
            return [{'kind':'macro_forge','request':deepcopy(template),'preparation':
                     {'source':label,'act':0,'index':0}} for _ in range(5)]
        with patch.object(PreparationCandidates,'observe',flood):
            old,requests=run(evaluations=30,gold_shop_routes=True)
            self.assertFalse(any(k=='macro_focus' for k,_ in requests))
            new,requests=run(evaluations=30,gold_shop_routes=True,macro_fair=True)
        core=[i for i,(k,_) in enumerate(requests) if k=='macro_focus' or k in ('macro_deck','macro_route','macro_resources','macro_failure','macro_exploration')]
        self.assertGreater(len(core),0)
        self.assertLessEqual(core[0],5)
        self.assertTrue(all(b-a<=5 for a,b in zip(core,core[1:])))
        self.assertEqual((new['status'],new['lower_bound'],new['upper_bound']),('UNKNOWN',0,1))
        self.assertGreater(new['search_metrics']['macro_service']['forced_macro_opportunities'],0)

    def test_legacy_disabled_path_preserved(self):
        _,a=run(evaluations=30)
        _,b=run(evaluations=30,macro_fair=False,macro_plateau=False,macro_widening=False,macro_routes='off')
        self.assertEqual([(k,canonical(r)) for k,r in a],[(k,canonical(r)) for k,r in b])

    def test_allocation_paths_deterministic_under_i085_switches(self):
        args=dict(evaluations=40,macro_fair=True,macro_plateau=True,macro_widening=True)
        _,a=run(**args);_,b=run(**args)
        self.assertEqual([(k,canonical(r)) for k,r in a],[(k,canonical(r)) for k,r in b])

    def test_profile_does_not_change_tactical_or_synthetic_settings(self):
        def effective(profile):
            return effective_parameters(['--out','unused',*settings('101',30,7,271828,profile)])
        a,b=effective('i082'),effective('i085')
        diffs={k for k in a if a[k]!=b[k]}
        self.assertEqual(diffs,{'feature_profile',*I085_PLANNER})
        self.assertEqual(b['macro_routes'],'shadow')
        self.assertEqual(b['dispatch_window'],56)

    def test_profile_explicit_overrides_win(self):
        a=effective_parameters(['--out','unused',*settings('101',30,7,271828,'i085'),
            '--no-macro-plateau','--macro-routes','on','--dispatch-window','14'])
        self.assertFalse(a['macro_plateau']);self.assertEqual(a['macro_routes'],'on')
        self.assertEqual(a['dispatch_window'],14)

    def test_configs_validate_boolean_string_and_budget_boundaries(self):
        for bad in ({'macro_routes':'invalid'}, {'macro_plateau':1}, {'macro_widening':True},
            {'macro_fair':True}, {'macro_route_every':0}, {'macro_route_limit':True},
            {'macro_route_queue_mib':-1}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):SearchConfig(**bad)
        SearchConfig(scheduler='focus',macro_plateau=True,macro_widening=True,macro_fair=True)


if __name__=='__main__':unittest.main()
