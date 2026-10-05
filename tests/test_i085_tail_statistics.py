"""Constructed survival/restart arithmetic; no game outcomes."""
import unittest
from types import SimpleNamespace
from tools.tail_statistics import survival_summary, restart_expectation, scaling_report
from spire_exact.planning.gate_timing import GateTiming
from test_focus_search import world
from test_i085_benchmark import row

class SurvivalTests(unittest.TestCase):
    def test_censoring_does_not_turn_into_event(self):
        r=survival_summary([{'seconds':10,'event':True},{'seconds':60,'event':False}],60)
        self.assertEqual(r['events'],1);self.assertEqual(r['censored'],1)
        self.assertAlmostEqual(r['rmst_seconds'],35)
        self.assertIsNone(r['quantiles_seconds']['0.9'])
        self.assertEqual(r['survival_at_horizon'],.5)

    def test_tied_events_precede_censoring(self):
        r=survival_summary([{'seconds':10,'event':True},{'seconds':10,'event':False}],10)
        self.assertEqual(r['survival_at_horizon'],.5)

    def test_zero_time_success(self):
        r=survival_summary([{'seconds':0,'event':True}],60)
        self.assertEqual(r['rmst_seconds'],0);self.assertEqual(r['quantiles_seconds']['0.95'],0)

    def test_no_extrapolation_after_early_censor(self):
        r=survival_summary([{'seconds':10,'event':False}],60)
        self.assertIsNone(r['rmst_seconds']);self.assertIsNone(r['survival_at_horizon'])

    def test_invalid_duration_and_bool_are_rejected(self):
        for t in (-1,True,float('nan'),float('inf')):
            with self.assertRaises(ValueError):survival_summary([{'seconds':t,'event':True}],60)

    def test_restart_benefit_and_overhead_harm(self):
        d=[(1,.1),(100,.9)]
        self.assertEqual(restart_expectation(d,1),10)
        self.assertAlmostEqual(restart_expectation(d,1,20),190)
        self.assertIsNone(restart_expectation([(100,1)],10))
        self.assertEqual(restart_expectation([(100,1)],100,20),100)

    def test_report_drops_invalid_runs_not_as_losses(self):
        r=row('i082');r.update(resource_valid=True,stop_reason='time_budget')
        missing={'id':'missing','arm':'i082','wall_cap_seconds':60}
        x=scaling_report([r,missing],['i082'])['i082']
        self.assertEqual(x['declared_runs'],2);self.assertEqual(x['auditable_runs'],1)
        self.assertEqual(len(x['omitted']),1)
        self.assertEqual(x['by_independent_cap']['60']['censored'],1)

    def test_early_evaluation_stop_is_not_censor_at_wall_cap(self):
        r=row('i082');r.update(resource_valid=True,stop_reason='evaluation_budget')
        x=scaling_report([r],['i082'])['i082']
        self.assertEqual(x['auditable_runs'],0)

    def test_bad_win_not_success(self):
        r=row('i082',True,10);r.update(resource_valid=True,verified_evidence=False)
        x=scaling_report([r],['i082'])['i082']
        self.assertEqual(x['auditable_runs'],0)

class GateTimingTests(unittest.TestCase):
    def test_real_outcomes_and_cache_are_distinct(self):
        c=GateTiming(True);bad=world([]);c.observe(bad,3);c.observe(bad,5)
        self.assertEqual(c.snapshot()['distinct_entries']['(0, 0)'],1)
        self.assertEqual(c.first_entry['(0, 0)'],3)
        good=world([{'kind':'card_reward','card':'GOOD'}]*2)
        c.observe(good,7,cache_hit=True);self.assertFalse(c.first_pass)
        c.observe(good,9);self.assertEqual(c.first_pass['(0, 0)'],9)

    def test_synthetic_never_passes(self):
        c=GateTiming(True);r=world([{'kind':'card_reward','card':'GOOD'}]*2);r['synthetic']=True
        c.observe(r,9);self.assertFalse(c.entries)

    def test_fit_inspection_does_not_call_refit(self):
        c=GateTiming(True);g=SimpleNamespace(fitted=8,entries={i:None for i in range(8)})
        c.fitted(SimpleNamespace(gates={(2,1):g}),None,15)
        c.fitted(SimpleNamespace(gates={(2,1):g}),None,20)
        self.assertEqual(c.first_fit['(2, 1)'],{'seconds':15,'rows':8})

if __name__=='__main__':unittest.main()

class IncompleteGateTests(unittest.TestCase):
    def test_known_F2_entry_with_unknown_outcome_is_reach_not_pass(self):
        from tools.i085_tail_study import oracle
        from spire_exact.planning.gate_timing import GateTiming
        from spire_exact.planning.indexed_strategy import PrefixTrie
        result,_=oracle([],'unsolved',1)
        result.update(status='BUDGET',value=None,observation=result['decision_evidence'][-1]['observation'])
        result.pop('terminal_combat',None)
        g=GateTiming(True,prefixes=PrefixTrie());g.observe(result,5)
        self.assertIn('(2, 1)',g.first_entry);self.assertNotIn('(2, 1)',g.first_pass)
        self.assertIn('(2, 0)',g.first_pass)
        self.assertTrue(all(type(x) is int for values in g.entries.values() for x in values))

    def test_feedback_gate_requires_campaign_metadata_not_assumed_act(self):
        from tools.i085_tail_study import oracle
        from spire_exact.planning.tail_search import TailFocusScheduler
        result,_=oracle([],'unsolved',1);a=TailFocusScheduler();a.add(result,'r')
        self.assertTrue(a.feedback_ready)
        result.pop('campaign');b=TailFocusScheduler();b.add(result,'r')
        self.assertFalse(b.feedback_ready)

class ConditionalTailTests(unittest.TestCase):
    def test_different_game_seeds_are_not_called_restart_samples(self):
        a=row('i082');a.update(resource_valid=True,stop_reason='time_budget',seed='A')
        b=dict(a,id='other',seed='B')
        r=scaling_report([a,b],['i082'])['i082']
        self.assertEqual(set(r['per_game_seed']),{'A','B'})
        self.assertEqual(r['per_game_seed']['A']['60']['n'],1)

    def test_fit_before_F1_keeps_negative_relative_time(self):
        a=row('i082');a.update(resource_valid=True,stop_reason='time_budget',seed='A',search_metrics={'gate_timing':{
            'final_act':2,'first_pass':{'(2, 0)':30},'first_fit':{},'joint_first_fit':{'seconds':20,'rows':24}}})
        d=scaling_report([a],['i082'])['i082']['fit_latency_diagnostics'][0]
        self.assertEqual(d['joint_fit_minus_F1_seconds'],-10)
        self.assertTrue(d['F2_right_censored']);self.assertIsNone(d['real_F2_first_fit'])
