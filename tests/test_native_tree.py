import unittest
from spire_exact.planning.native_tree import NativeTreeScheduler,observed_delta

class NativeTreeTests(unittest.TestCase):
    def test_real_resource_cost_and_card_changes_are_observed_not_name_rules(self):
        before={'hp':'80','max_hp':'80','gold':99,'deck':[{'id':'A'},{'id':'B'}],'relics':[]}
        after={'hp':'64','max_hp':'80','gold':99,'deck':[],'relics':['arbitrary_new_relic']}
        d=observed_delta(before,after)
        self.assertEqual(d['hp'],'-16');self.assertEqual(len(d['deck']['removed']),2)
        self.assertEqual(len(d['relics']['added']),1)
    def test_arbitrary_native_action_symbols_are_supported(self):
        actions=[{'kind':'event','native_key':'unknown_A'},{'kind':'event','native_key':'unknown_B'}]
        result={'status':'TERMINAL','value':[0],'trace':[actions[0]],'observation':{'floor':5},
                'decision_evidence':[{'phase':'event','observation':{'floor':1},'available_actions':actions}]}
        s=NativeTreeScheduler();s.add(result,'a');p=s.next()
        self.assertEqual(p['prefix'],[actions[1]])
        self.assertFalse(s.nodes[next(iter(s.nodes))].actions[next(iter(s.nodes[next(iter(s.nodes))].actions))].native_win_candidates)
    def test_full_history_keeps_same_observation_nodes_separate(self):
        s=NativeTreeScheduler()
        for x in (1,2):
            s.add({'status':'BUDGET','trace':[{'kind':'play','x':x},{'kind':'map','x':0}],
                'observation':{'floor':2},'decision_evidence':[{'phase':'combat'},
                    {'phase':'map','observation':{'floor':1},'available_actions':[{'kind':'map','x':0},{'kind':'map','x':1}]}]},str(x))
        self.assertEqual(len(s.nodes),2)
        self.assertTrue(all(e.complete==0 for n in s.nodes.values() for e in n.actions.values()))
    def test_changed_menu_at_identical_history_is_reported(self):
        s=NativeTreeScheduler();r={'status':'TERMINAL','value':[0],'trace':[{'kind':'event','index':0}],
            'observation':{'floor':1},'decision_evidence':[{'phase':'event','observation':{'floor':1},
            'available_actions':[{'kind':'event','index':0},{'kind':'event','index':1}]}]}
        s.add(r,'first');r['decision_evidence'][0]['available_actions'][1]={'kind':'event','index':2};s.add(r,'second')
        self.assertEqual(s.errors[0]['reason'],'NATIVE_MENU_DIVERGENCE')

if __name__=='__main__':unittest.main()
