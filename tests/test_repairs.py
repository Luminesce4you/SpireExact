import unittest
from pathlib import Path
from spire_exact.planning.repairs import RepairQueue,deep_repair
from spire_exact.planning.search import Evaluator,SearchConfig

def probe(floor,room):return {'repair':{'floor':floor,'room':room}}

class RepairTests(unittest.TestCase):
    def test_final_boss_cannot_starve_behind_old_fights(self):
        q=RepairQueue('deep_boss')
        for floor,room in [(8,'Elite'),(17,'Boss'),(33,'Boss'),(46,'Monster'),(48,'Boss')]:q.append(probe(floor,room))
        self.assertEqual([q.popleft()['repair']['floor']for _ in range(5)],[48,33,17,8,46])
    def test_default_queue_remains_fifo(self):
        q=RepairQueue();q.append(probe(8,'Elite'));q.append(probe(48,'Boss'))
        self.assertEqual(q.popleft()['repair']['floor'],8)
    def test_final_only_mode_preserves_all_early_repair_order(self):
        q=RepairQueue('deep_final_boss')
        for floor,room in [(8,'Elite'),(17,'Boss'),(33,'Boss')]:q.append(probe(floor,room))
        final=probe(48,'Boss');final['repair']['deep']=True;q.append(final)
        self.assertEqual([q.popleft()['repair']['floor']for _ in range(4)],[48,8,17,33])
    def test_equal_priority_preserves_discovery_order(self):
        q=RepairQueue('deep_boss');a=probe(48,'Boss');b=probe(48,'Boss')
        q.append(a);q.append(b);self.assertIs(q.popleft(),a);self.assertIs(q.popleft(),b)
    def test_deep_override_does_not_change_original_advisor_or_ordinary_budget(self):
        advisor={'budget_ms':400,'boss_budget_ms':1600}
        ev=Evaluator(None,dict(seed='0',character='IRONCLAD',ascension=0,unlocks='all'),Path('.'),SearchConfig(),advisor)
        request=ev.request([],advisor_scale=3,boss_budget_ms=30000)
        self.assertEqual(advisor['boss_budget_ms'],1600)
        self.assertEqual(request['advisor']['budget_ms'],1200)
        self.assertEqual(request['advisor']['boss_budget_ms'],30000)
        self.assertEqual(ev.request([],advisor_scale=3)['advisor']['boss_budget_ms'],4800)
    def test_invalid_repair_mode_is_rejected(self):
        with self.assertRaises(ValueError):SearchConfig(repair_mode='unsafe')
    def test_final_boss_policy_does_not_spend_deep_budget_in_early_acts(self):
        for act in (0,1):self.assertFalse(deep_repair('deep_final_boss',{'room':'Boss','act':act}))
        self.assertTrue(deep_repair('deep_final_boss',{'room':'Boss','act':2}))
        self.assertFalse(deep_repair('deep_final_boss',{'room':'Elite','act':2}))
        self.assertFalse(deep_repair('fifo',{'room':'Boss','act':2}))

if __name__=='__main__':unittest.main()
