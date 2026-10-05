"""Anti-tail scheduler tests: fabricated trajectories, never native evidence."""
import copy
import math
import unittest
from unittest.mock import patch

from spire_exact.canonical import canonical
from spire_exact.planning.tail_search import ServiceClock, TailFocusScheduler
from spire_exact.planning.focus import FocusScheduler
from spire_exact.planning.search import SearchConfig
from test_focus import trajectory


class ClockTests(unittest.TestCase):
    def test_new_flow_does_not_start_at_zero(self):
        c=ServiceClock()
        for _ in range(20):
            key=c.choose(['old']);c.charge(key,10)
        c.activate(['old','new'])
        self.assertGreaterEqual(c.tags['new'],190)

    def test_continuous_arrivals_do_not_starve_old_flow(self):
        c=ServiceClock();c.activate(['old']);chosen=[]
        for i in range(60):
            keys=['old',f'new-{i:03d}']
            k=c.choose(keys);chosen.append(k);c.charge(k,1)
        self.assertGreater(chosen.count('old'),10)

    def test_pending_reservations_diversify_batch(self):
        c=ServiceClock();picks=[]
        for _ in range(8):
            k=c.choose(['a','b']);c.charge(k,3);picks.append(k)
        self.assertEqual(picks.count('a'),4)
        self.assertEqual(picks.count('b'),4)

    def test_no_negative_or_nonfinite_costs(self):
        for x in (-1,True,float('nan'),float('inf')):
            with self.subTest(x=x),self.assertRaises(ValueError):ServiceClock().charge('a',x)

    def test_refunds_do_not_reverse_virtual_floor(self):
        c=ServiceClock();c.charge('a',3);c.choose(['a']);v=c.floor
        c.correct('a',-3)
        self.assertEqual(c.floor,v);self.assertGreaterEqual(c.tags['a'],v)


class TailSchedulerTests(unittest.TestCase):
    def new(self,**kw):return TailFocusScheduler(plateau_diversity=True,recover_deferred=True,**kw)
    def settle(self,s,g,cost=1):
        spec={'group':g}
        s._settle(spec,{'performance':{'wall_us':int(cost*1e6)}})

    def test_no_repeat_and_exact_legality(self):
        r=trajectory(floors=6,options=5);s=self.new();s.add(r,'r');seen=set()
        for _ in range(100):
            g=s.next()
            if g is None:break
            p=g['prefix'];i=g['index']
            self.assertEqual(p[:-1],r['trace'][:i]);self.assertIn(p[-1],r['decision_evidence'][i]['available_actions'])
            self.assertNotIn(canonical(p),seen);seen.add(canonical(p));self.settle(s,g)
        self.assertEqual(len(seen),48)

    def test_early_act_gets_service_without_gate_fit(self):
        s=self.new();s.add(trajectory(floors=33,options=5),'r');early=[]
        for _ in range(16):
            g=s.next();early.append(g.get('act'));self.settle(s,g)
        self.assertIn(0,early)
        self.assertIsNone(s.models)

    def test_submitted_tail_work_has_cohort_cap(self):
        s=self.new(tail_cohort_inflight=1);s.add(trajectory(floors=12,options=10),'r')
        groups=[s.next() for _ in range(20)]
        tail=[g for g in groups if g and g.get('tail_lane')=='tail']
        self.assertEqual(len(tail),1)
        self.assertEqual(sum(s.cohort_pending.values()),1)
        self.settle(s,tail[0],9)
        self.assertEqual(sum(s.cohort_pending.values()),0)

    def test_real_cost_cache_and_unknown_are_distinct(self):
        s=self.new();s.add(trajectory(floors=4),'r');g=s.next()
        self.settle(s,g,7);self.assertEqual(s.measured,7)
        g=s.next();s._settle({'group':g,'cache_hit':True},{'performance':{'wall_us':999999}})
        self.assertEqual(s.measured,7);self.assertEqual(s.estimated,0)
        g=s.next();s._settle({'group':g},{})
        self.assertEqual(s.estimated,7);self.assertEqual(s.stats['unknown_costs'],1)

    def test_duplicate_settlement_is_ignored(self):
        s=self.new();s.add(trajectory(floors=3),'r');g=s.next()
        self.settle(s,g,2);self.settle(s,g,2)
        self.assertEqual(s.measured,2)

    def test_refund_keeps_tail_branch_unresolved(self):
        s=self.new();s.add(trajectory(floors=3),'r')
        g=s._tail_pick(s._candidates());key=g['tail_child'];s.release_unsubmitted({'group':g})
        self.assertTrue(s.explorer.pending(key))
        self.assertFalse(s.tickets)

    def test_different_tactical_prefixes_share_only_cohort(self):
        s=self.new()
        a=trajectory(floors=4,tag=1);b=copy.deepcopy(a)
        # Different exact combat actions before the same strategic choices.
        for r,v in ((a,1),(b,2)):
            r['trace'].insert(0,{'kind':'end_turn','fixture':v})
            r['decision_evidence'].insert(0,{'phase':'combat','observation':{},'available_actions':[]})
        s.add(a,'a');s.add(b,'b')
        self.assertEqual(len(s.sites),16)
        self.assertEqual({x.cohort for x in s.sites.values()},{0})

    def test_scalar_damage_improvement_does_not_reset_service(self):
        s=self.new();r=trajectory(floors=7,death_hp=90);s.add(r,'r')
        for _ in range(8):
            g=s.next();self.settle(s,g,2)
        before=dict(s.lanes.tags)
        s.add(trajectory(floors=7,death_hp=1),'better-damage')
        self.assertEqual(before,s.lanes.tags)

    def test_synthetic_and_unknown_not_archived(self):
        for update in ({'synthetic':True},{'status':'UNKNOWN','value':None}):
            s=self.new();r=trajectory(floors=3);r.update(update);s.add(r,'bad')
            self.assertFalse(s.sites)

    def test_cache_does_not_add_new_tail_sites(self):
        s=self.new();s.add(trajectory(floors=3),'cache',spec={'cache_hit':True})
        self.assertFalse(s.sites)

    def test_full_source_guard_not_approximate_state(self):
        s=self.new();r=trajectory(floors=3);s.add(r,'a');b=copy.deepcopy(r)
        b['decision_evidence'][0]['observation']['hidden_counter_fixture']=99;s.add(b,'b')
        self.assertGreater(s.stats['source_guard_mismatch'],0)

    def test_archive_eviction_not_proof_and_bounded_payload(self):
        s=self.new(tail_sites=4);s.add(trajectory(floors=10),'r')
        self.assertEqual(len(s.sites),4);self.assertGreater(s.stats['site_evictions'],0)
        self.assertLessEqual(s.payload_bytes,s.byte_limit)
        self.assertFalse(s.snapshot()['tail_search']['proof_changed'])

    def test_off_is_original_sequence(self):
        a=FocusScheduler();b=TailFocusScheduler(tail_mode='off');r=trajectory(floors=7)
        a.add(r,'r');b.add(r,'r')
        for _ in range(20):self.assertEqual(a.next(),b.next())

    def test_shadow_cannot_consume_actual_branches(self):
        a=FocusScheduler();b=TailFocusScheduler(tail_mode='shadow');r=trajectory(floors=7)
        a.add(r,'r');b.add(r,'r')
        for _ in range(20):self.assertEqual(a.next(),b.next())
        self.assertFalse(b.tickets)

    def test_pause_is_reversible(self):
        s=self.new();s.add(trajectory(floors=5),'r')
        with patch.object(s,'dispatch_blocked',return_value=True):self.assertEqual(dict(s._candidates()),{})
        self.assertTrue(s._candidates())

    def test_defaults_and_validation(self):
        self.assertEqual(SearchConfig().tail_mode,'off')
        SearchConfig(scheduler='focus',tail_mode='on')
        for kw in ({'tail_mode':'bad'},{'tail_mode':'on'},{'tail_sites':0},{'tail_feedback_rounds':True}):
            with self.subTest(kw=kw),self.assertRaises(ValueError):SearchConfig(**kw)

if __name__=='__main__':unittest.main()

class RetentionAndWiringTests(unittest.TestCase):
    def test_late_arrivals_cannot_fifo_evict_every_early_root(self):
        s=TailFocusScheduler(tail_sites=12)
        s.add(trajectory(floors=5),'early')
        for tag in range(1,12):
            r=trajectory(floors=30,tag=tag)
            for e in r['decision_evidence']:
                e['observation']['act']=2
            r['observation']['act']=2
            s.add(r,str(tag))
        self.assertTrue(any(site.act==0 for site in s.sites.values()))
        self.assertTrue(any(site.act==2 for site in s.sites.values()))
        self.assertLessEqual(len(s.sites),12)

    def test_main_loop_cost_settlement_and_no_win_claim(self):
        from test_focus_search import run
        r,q=run(evaluations=40,tail_mode='on',gate_timing=True,macro_fair=True)
        self.assertEqual(r['status'],'UNKNOWN');self.assertEqual(r['upper_bound'],1)
        self.assertIn('macro_tail',{kind for kind,_ in q})
        stats=r['search_metrics']['tail_search']
        self.assertEqual(stats['pending_tickets'],0)
        self.assertGreater(stats['counts']['settled'],0)

    def test_threshold_override_reaches_both_regressions(self):
        from tools.run_release_source import effective_parameters
        p=effective_parameters(['--feature-profile','i085','--out','unused',
            '--gate-model-minimum','8','--gate-model-refresh','4'])
        self.assertEqual(p['gate_model_minimum'],8);self.assertEqual(p['gate_model_refresh'],4)

class TailStudyTests(unittest.TestCase):
    def test_fabricated_goal_cannot_become_native_winner(self):
        from tools.i085_tail_study import oracle
        from spire_exact.mode1 import is_winning_candidate
        result,_=oracle([{'kind':'card_reward','index':1,'card':'X0_1'},
                         {'kind':'card_reward','index':1,'card':'X1_1'}],'early_pair',4)
        self.assertTrue(result['toy_goal']);self.assertFalse(is_winning_candidate(result))

    def test_simulation_cutoff_censors_inflight_goal(self):
        from tools.i085_tail_study import experiment
        result=experiment('late_single',2,'i085-tail',14,budget=30,seconds=.01)
        self.assertFalse(result['goal_found']);self.assertEqual(result['events'],[])
        self.assertEqual(result['simulated_observed_seconds'],.01)

    def test_new_profile_keeps_tail_control_experimental(self):
        from tools.run_release_source import effective_parameters
        a=effective_parameters(['--feature-profile','i085','--out','unused'])
        self.assertEqual(a['tail_mode'],'shadow')

    def test_ancestors_keep_exact_real_suffix_without_terminal_damage_rank(self):
        s=TailFocusScheduler();r=trajectory(floors=7);s.add(r,'a')
        before={n:v.achievement for n,v in s.sites.items()}
        more=copy.deepcopy(r);more['observation']['floor']=11
        s.add(more,'b')
        for n,v in s.sites.items():self.assertGreaterEqual(v.achievement,before.get(n,()))

    def test_compaction_does_not_forget_outstanding_ticket_clocks(self):
        s=TailFocusScheduler(tail_sites=8);s.add(trajectory(floors=3),'r')
        g=s._tail_pick(s._candidates());ticket=s.tickets[g['tail_ticket']]
        s.add(trajectory(floors=10,tag=1),'new')
        for c,k in ticket[1]:self.assertIn(k,c.tags)
        s._settle({'group':g},{'performance':{'wall_us':2000000}})
        self.assertEqual(s.measured,2)

class FinalWiringTests(unittest.TestCase):
    def test_feedback_window_shrinks_only_after_observed_final_F1(self):
        import test_focus_search as fixture
        from tools.i085_tail_study import oracle
        original=fixture.FakeEvaluator.dispatch;peaks=[]
        def dispatch(ev,specs):
            r=original(ev,specs);peaks.append(ev.pending_count);return r
        def world(history,*args):return oracle(history,'unsolved',1)[0]
        with patch.object(fixture,'world',world),patch.object(fixture.FakeEvaluator,'dispatch',dispatch):
            report,_=fixture.run(evaluations=30,dispatch_window=56,tail_mode='on')
        self.assertLessEqual(max(peaks),4)  # 2 fake workers * 2 rounds
        self.assertTrue(report['search_metrics']['tail_search']['feedback_ready'])

    def test_thresholds_reach_actual_gate_and_joint_instances(self):
        import test_focus_search as fixture
        import spire_exact.planning.gatemodel as module
        from spire_exact.planning.f2_joint_model import F2JointModel
        old=module.GateModels;gates=[];joints=[]
        def gate(*args,**kwargs):
            obj=old(*args,**kwargs);gates.append(obj);return obj
        def joint():
            obj=F2JointModel();joints.append(obj);return obj
        with patch.object(module,'GateModels',gate),patch('spire_exact.planning.search.F2JointModel',joint):
            fixture.run(evaluations=12,gate_model_minimum=8,gate_model_refresh=4,
                        f2_readiness_probes=True,f2_joint_model=True)
        self.assertEqual((gates[0].minimum,gates[0].refresh),(8,4))
        self.assertEqual((joints[0].minimum,joints[0].refresh),(8,4))

class RetryCostTests(unittest.TestCase):
    def test_requeue_is_billed_as_a_new_attempt_without_mutating_old_ticket(self):
        s=TailFocusScheduler();s.add(trajectory(floors=3),'r');group=s._tail_pick(s._candidates())
        original=group['tail_ticket'];s._settle({'group':group},{'performance':{'wall_us':5000000}})
        retry={'group':group,'requeued':1};s.prepare_submission(retry)
        self.assertEqual(group['tail_ticket'],original)
        self.assertNotEqual(retry['group']['tail_ticket'],original)
        s._settle(retry,{'performance':{'wall_us':7000000}})
        self.assertEqual(s.measured,12);self.assertFalse(s.tickets)

    def test_retry_preparation_is_idempotent(self):
        s=TailFocusScheduler();s.add(trajectory(floors=3),'r');g=s._tail_pick(s._candidates())
        s._settle({'group':g},{})
        retry={'group':g,'requeued':1};s.prepare_submission(retry);first=retry['group']['tail_ticket']
        s.prepare_submission(retry)
        self.assertEqual(retry['group']['tail_ticket'],first);self.assertEqual(len(s.tickets),1)

    def test_unaccepted_retry_does_not_undo_prior_submission_identity(self):
        s=TailFocusScheduler();s.add(trajectory(floors=3),'r');g=s._tail_pick(s._candidates())
        child=g['tail_child'];s._settle({'group':g},{})
        retry={'group':g,'requeued':1};s.prepare_submission(retry);s.release_unsubmitted(retry)
        self.assertIn(child,s.explorer.submitted);self.assertFalse(s.tickets)

class ThresholdTriggerTests(unittest.TestCase):
    def test_minimum_alone_does_not_override_real_gate_dirty_threshold(self):
        from test_gatemodel import rollout
        from spire_exact.planning.gatemodel import GateModels
        slow=GateModels(minimum=8,refresh=16);fast=GateModels(minimum=8,refresh=4)
        for i in range(8):
            r=rollout(['A'],True,tag=i,hp_after=40+i)
            slow.add(r);fast.add(r)
        self.assertEqual(slow.gates[(0,0)].fitted,0)
        self.assertEqual(fast.gates[(0,0)].fitted,8)
        for i in range(8,16):slow.add(rollout(['A'],True,tag=i,hp_after=40+i))
        self.assertEqual(slow.gates[(0,0)].fitted,16)

    def test_joint_first_fit_uses_a_different_refresh_condition(self):
        from test_f2_joint_model import fit_joint
        from spire_exact.planning.f2_joint_model import F2JointModel
        j=F2JointModel();j.minimum=8;j.refresh=16
        fit_joint(j,count=8)
        self.assertEqual(j.fitted,8)

class RequeueIntegrationTests(unittest.TestCase):
    def test_main_loop_requeue_gets_fresh_service_ticket(self):
        import test_focus_search as fixture
        from tools.i085_tail_study import oracle
        failed=[]
        def world(history,*args):
            if history and not failed:
                failed.append(True)
                return {'status':'UNKNOWN','reason':'NATIVE_TASK_MEMORY_BUDGET','value':None,
                        'trace':[],'decision_evidence':[],'observation':None}
            return oracle(history,'unsolved',1)[0]
        with patch.object(fixture,'world',world):
            r,_=fixture.run(evaluations=30,dispatch_window=4,tail_mode='on',requeue_lost=1)
        self.assertTrue(failed)
        self.assertEqual(r['requeued_evaluations']['NATIVE_TASK_MEMORY_BUDGET'],1)
        self.assertEqual(r['search_metrics']['tail_search']['counts']['retry_reservations'],1)
