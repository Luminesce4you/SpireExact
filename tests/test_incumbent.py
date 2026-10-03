import unittest
from spire_exact.planning.incumbent import IncumbentScheduler,native_progress

def trajectory(floor=17,opening='A'):
    trace=[{'kind':'event','key':opening},{'kind':'map','index':0}]
    evidence=[{'phase':'event','observation':{'floor':0,'act':0},'available_actions':[
        trace[0],{'kind':'event','key':'alternative'}]},
        {'phase':'map','observation':{'floor':1,'act':0},'available_actions':[
            trace[1],{'kind':'map','index':1}]}]
    for hp in [100,40]:
        trace.append({'kind':'end_turn'})
        evidence.append({'phase':'combat','observation':{'floor':floor,'act':int(floor>17),'room':'Boss',
            'hp':'20','max_hp':'80','enemies':[{'id':'boss','combat_id':1,'hp':str(hp)}]}})
    return {'status':'TERMINAL','value':[0],'native_terminal_observed':True,'trace':trace,
            'decision_evidence':evidence,'observation':{'floor':floor,'act':int(floor>17),'hp':'0','max_hp':'80','room':'Boss'}}

class IncumbentTests(unittest.TestCase):
    def test_unknown_and_timeout_cannot_be_a_completed_elite(self):
        r=trajectory(48);r.update(status='UNKNOWN',reason='NATIVE_WORKER_TIMEOUT')
        s=IncumbentScheduler();s.add(r,'timeout')
        self.assertIsNone(native_progress(r));self.assertEqual(s.elite,set())
    def test_enemy_progress_uses_observations_only_and_rejects_replacement(self):
        r=trajectory();self.assertAlmostEqual(native_progress(r)[-1],.6)
        r['decision_evidence'][-1]['observation']['enemies'][0]['combat_id']=2
        self.assertEqual(native_progress(r)[-1],0)
    def test_better_continuation_updates_exact_ancestor_siblings(self):
        s=IncumbentScheduler();s.add(trajectory(17),'early');before=len(s.branches)
        s.add(trajectory(48),'late')
        self.assertEqual(len(s.branches),before)
        self.assertTrue(all(b.source_label=='late' for b in s.branches))
        self.assertGreater(s.credited,0)
        p=s.next();self.assertEqual(p['source'],'late')
        self.assertEqual(p['prefix'][:-1],trajectory(48)['trace'][:p['index']])
    def test_same_observation_different_history_does_not_receive_credit(self):
        s=IncumbentScheduler();s.add(trajectory(17,'A'),'early')
        original=next(b for b in s.branches if b.index==1)
        s.add(trajectory(48,'B'),'late')
        self.assertEqual(original.source_label,'early')
    def test_nonelite_alternatives_remain_pending(self):
        s=IncumbentScheduler();s.add(trajectory(17,'old'),'old')
        for i in range(5):s.add(trajectory(40+i,str(i)),str(i))
        self.assertNotIn('old',s.elite)
        self.assertTrue(any(b.source_label=='old' and b.index==1 for b in s.branches))
        self.assertFalse(s.snapshot()['source_quality_is_proof'])

if __name__=='__main__':unittest.main()
