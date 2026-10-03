import copy
from pathlib import Path
import unittest
from spire_exact.canonical import ContractError,canonical
from spire_exact.planning.indexed_strategy import IndexedStrategicScheduler
from spire_exact.planning.search import Evaluator,SearchConfig
from tests.test_strategy import trajectory


class DiagnosticScopeTests(unittest.TestCase):
    def test_final_boss_map_boundary_is_not_whole_run_victory(self):
        from tools.diagnose_a10_preparation import rescued
        boundary={'status':'BUDGET','phase':'map','reason':'candidate_horizon','observation':{'floor':49,'hp':'11'}}
        self.assertFalse(rescued(boundary,49))
        boundary['observation']['floor']=33
        self.assertTrue(rescued(boundary,33))
    def test_all_macro_proposals_preserve_earlier_act_actions(self):
        result=trajectory();prefix=copy.deepcopy(result['trace'][:2])
        scheduler=IndexedStrategicScheduler(prefix);scheduler.add(result,'root')
        count=0
        while (proposal:=scheduler.next()) is not None:
            count+=1
            self.assertGreaterEqual(proposal['index'],2)
            self.assertEqual(canonical(proposal['prefix'][:2]),canonical(prefix))
        self.assertGreater(count,0)
        foreign=copy.deepcopy(result);foreign['trace'][0]={'kind':'foreign'}
        with self.assertRaises(ContractError):scheduler.add(foreign,'outside')

    def test_every_request_and_horizon_respects_diagnostic_scope(self):
        prefix=[{'kind':'map','row':1}]
        ctx=dict(seed='0',character='IRONCLAD',ascension=10,unlocks='all')
        ev=Evaluator(None,ctx,Path('.'),SearchConfig(),scope_prefix=prefix,stop_floor=33)
        self.assertEqual(ev.request(prefix)['stop_at_floor'],33)
        self.assertEqual(ev.request(prefix,horizon=100)['stop_at_floor'],33)
        self.assertEqual(ev.request(prefix,horizon=17)['stop_at_floor'],17)
        with self.assertRaises(ContractError):ev.request([])
        with self.assertRaises(ContractError):ev.request([{'kind':'map','row':2}])
        prefix[0]['row']=99
        self.assertEqual(ev.request([{'kind':'map','row':1}])['history'],[{'kind':'map','row':1}])
