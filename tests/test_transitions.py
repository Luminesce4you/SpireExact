import unittest
from types import SimpleNamespace
from spire_exact.planning.transitions import NativeTransitionOracle
from spire_exact.canonical import ContractError

class TransitionTests(unittest.TestCase):
    def test_crash_does_not_become_a_fictitious_inventory_loss(self):
        ev=SimpleNamespace(request=lambda p:{'history':p},
            batch=lambda specs:[(specs[0],{'status':'UNKNOWN','reason':'NATIVE_WORKER_CRASH'},'x')])
        action={'kind':'event','index':0}
        state={'status':'DECISION','trace':[],'actions':[action],
               'observation':{'hp':'80','deck':[{'id':'A'}],'relics':['B']}}
        result=NativeTransitionOracle(ev).apply(state,action)
        self.assertIsNone(result['observed_delta']);self.assertFalse(result['effects_are_native'])
    def test_only_exact_native_menu_actions_can_be_requested(self):
        oracle=NativeTransitionOracle(None)
        with self.assertRaises(ContractError):
            oracle.apply({'status':'DECISION','trace':[],'actions':[{'kind':'event','index':0}]},
                         {'kind':'event','index':1})
