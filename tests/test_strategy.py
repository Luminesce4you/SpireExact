import unittest
from spire_exact.planning.strategy import StrategicScheduler,DepthFirstScheduler,FailureAnalyzer,strategic_phase,capability_signature

def trajectory():
    evidence=[];trace=[]
    for floor in range(1,34):
        for phase in ('map','card_reward','rewards','select_cards'):
            action={'kind':phase,'index':0};trace.append(action)
            evidence.append({'phase':phase,'observation':{'floor':floor,'act':int(floor>17),
                'room':'Combat','turn':1 if phase=='select_cards' else None},
                'available_actions':[action,{'kind':phase,'index':1}]})
    return {'status':'TERMINAL','value':[0],'trace':trace,'decision_evidence':evidence,
            'observation':{'floor':33,'act':1,'room':'Boss','hp':'0','max_hp':'80',
                           'strategic':{'scaling':'0','strength':'0','block_per_draw':'1'}}}

class StrategyTests(unittest.TestCase):
    def test_fatal_native_access_violation_has_a_distinct_engineering_class(self):
        from spire_exact.planning.archive import classify_failure
        self.assertEqual(classify_failure({'status':'UNKNOWN','reason':'NATIVE_WORKER_CRASH:exit=3221225477'}),'NATIVE_CRASH')
    def test_dfs_deepest_branch_then_new_suffix_before_old_siblings(self):
        import copy
        r=trajectory();s=DepthFirstScheduler();s.add(r,'root');first=s.next()
        self.assertEqual(first['floor'],33)
        child=copy.deepcopy(r)
        child['trace']=first['prefix']+[{'kind':'map','index':0}]
        child['decision_evidence']=child['decision_evidence'][:len(first['prefix'])]+[
            {'phase':'map','observation':{'floor':34,'act':2},'available_actions':[
                {'kind':'map','index':0},{'kind':'map','index':1}]}]
        s.add(child,'child');second=s.next()
        self.assertEqual(second['floor'],34);self.assertEqual(second['source'],'child')
        self.assertEqual(second['prefix'][:-1],child['trace'][:-1])
    def test_original_executed_sibling_is_not_rescheduled(self):
        import copy
        r=trajectory();s=DepthFirstScheduler();s.add(r,'root');first=s.next()
        child=copy.deepcopy(r);child['trace'][first['index']]=first['prefix'][-1]
        s.add(child,'child')
        rest=[]
        while (p:=s.next()) is not None:rest.append(p['prefix'])
        self.assertNotIn(r['trace'][:first['index']+1],rest)
    def test_menu_noise_and_combat_choices_are_not_macro_branches(self):
        s=StrategicScheduler();s.add(trajectory(),'a')
        proposals=[s.next() for _ in range(20)]
        self.assertTrue(all(x['phase'] in ('map','card_reward') for x in proposals))
        self.assertGreater(len({x['floor'] for x in proposals}),5)
        self.assertTrue(any(x['floor']<18 for x in proposals))
        self.assertLess(sum(x['floor']==33 for x in proposals),5)
    def test_proposals_replay_exact_prefix_then_only_legal_alternative(self):
        r=trajectory();s=StrategicScheduler();s.add(r,'a')
        for _ in range(10):
            p=s.next();i=p['index']
            self.assertEqual(p['prefix'][:-1],r['trace'][:i])
            self.assertIn(p['prefix'][-1],r['decision_evidence'][i]['available_actions'])
            self.assertNotEqual(p['prefix'][-1],r['trace'][i])
    def test_choice_purpose_prevents_upgrades_becoming_combat_exhaust(self):
        self.assertEqual(strategic_phase({'phase':'select_cards','observation':{'selection':{'purpose':'Upgrade'}}}),'deck')
        self.assertIsNone(strategic_phase({'phase':'select_cards','observation':{'selection':{'purpose':'Exhaust'},'turn':2}}))
    def test_same_trace_different_internal_prefix_action_remains_distinct_request(self):
        r=trajectory();s=StrategicScheduler();s.add(r,'a');n=len(s.branches)
        s.add(r,'again');self.assertEqual(len(s.branches),n)
        import copy
        r2=copy.deepcopy(r);r2['trace'][0]={'kind':'map','index':5};s.add(r2,'b')
        self.assertGreater(len(s.branches),n)
    def test_capability_bins_preserve_scaling_and_energy_difference(self):
        self.assertNotEqual(capability_signature({'strategic':{'strength':'8'}}),capability_signature({'strategic':{'energy_per_card':'1'}}))
    def test_failure_diagnosis_does_not_assert_infeasibility(self):
        d=FailureAnalyzer.analyze(trajectory())
        self.assertTrue(d['diagnosis_is_heuristic']);self.assertIn('insufficient_scaling',d['hypotheses'])
        self.assertNotIn('infeasible',d)

if __name__=='__main__':unittest.main()
