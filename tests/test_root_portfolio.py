import unittest
from spire_exact.planning.root_portfolio import root_requests
from spire_exact.canonical import ContractError

class Evaluator:
    def request(self, prefix):return {'history':prefix,'generate_candidate':True}

class RootPortfolioTests(unittest.TestCase):
    def test_uses_actual_menu_and_retains_instance_specific_fields(self):
        actions=[{'kind':'event','index':1,'key':'native.a'}, {'kind':'event','index':2,'key':'native.b'}]
        state={'status':'DECISION','trace':[],'actions':actions}
        roots,deferred=root_requests(state,Evaluator(),4)
        self.assertEqual([r['request']['history']for r in roots],[[a]for a in actions])
        self.assertEqual(deferred,0)
        roots[0]['request']['history'][0]['key']='mutated'
        self.assertEqual(actions[0]['key'],'native.a')

    def test_cap_is_reported_not_treated_as_exhaustion(self):
        roots,deferred=root_requests({'status':'DECISION','trace':[],'actions':[{'index':i}for i in range(5)]},Evaluator(),2)
        self.assertEqual((len(roots),deferred),(2,3))

    def test_refuses_old_prefix(self):
        with self.assertRaises(ContractError):root_requests({'status':'DECISION','trace':[{'a':1}],'actions':[{'b':2}]},Evaluator(),4)

    def test_unsupported_or_terminal_does_not_fabricate_opening(self):
        for status in ['UNKNOWN','UNSUPPORTED','TERMINAL']:
            self.assertEqual(root_requests({'status':status,'actions':[{'a':1}]},Evaluator(),4),([],0))

    def test_identical_labels_do_not_merge_distinct_native_actions(self):
        roots,_=root_requests({'status':'DECISION','trace':[],'actions':[{'label':'same','instance':1},{'label':'same','instance':2}]},Evaluator(),4)
        self.assertEqual(len(roots),2)

if __name__=='__main__':unittest.main()
