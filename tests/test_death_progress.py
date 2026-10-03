import copy,unittest
from spire_exact.planning.archive import utility,combat_loss_progress

def death(hp=50,turn=3):
    enemy=lambda value:{'id':'BOSS','combat_id':1,'hp':str(value)}
    start={'act':1,'floor':33,'turn':1,'enemies':[enemy(100)]}
    end={'act':1,'floor':33,'turn':turn,'enemies':[enemy(hp)]}
    return {'status':'TERMINAL','value':[0],'observation':{'act':1,'floor':33,'hp':'0','gold':0,'potions':[]},
        'decision_evidence':[{'phase':'combat','observation':start},{'phase':'combat','observation':end}],
        'terminal_combat':end}

class DeathProgressTests(unittest.TestCase):
    def test_observed_revival_keeps_completed_life_progress(self):
        a=death(0,3)
        respawn=copy.deepcopy(a['terminal_combat']);respawn['turn']=4;respawn['enemies'][0]['hp']='212'
        a['decision_evidence'].append({'phase':'combat','observation':respawn})
        a['terminal_combat']=copy.deepcopy(respawn);a['terminal_combat']['enemies'][0]['hp']='118'
        p=combat_loss_progress(a)
        self.assertEqual(p['revivals_observed'],1)
        self.assertEqual(p['observed_hp_removed'],194)
        self.assertAlmostEqual(p['hp_removed_fraction'],194/312)
        self.assertAlmostEqual(p['current_life_hp_removed_fraction'],94/212)
        self.assertGreater(utility(a),utility(death(1,30)))
    def test_healing_without_zero_is_not_a_revival(self):
        a=death(20);a['terminal_combat']=copy.deepcopy(a['terminal_combat']);a['terminal_combat']['enemies'][0]['hp']='70'
        p=combat_loss_progress(a)
        self.assertEqual(p['revivals_observed'],0);self.assertAlmostEqual(p['hp_removed_fraction'],.3)
    def test_unobserved_prior_life_is_not_credited(self):
        a=death(50)
        a['decision_evidence'][0]['observation']['enemies'][0]['hp']='0'
        p=combat_loss_progress(a)
        self.assertEqual(p['revivals_observed'],0);self.assertEqual(p['observed_hp_removed'],0)
    def test_multiple_enemy_lives_keep_separate_identity(self):
        a=death(0)
        a['decision_evidence'][0]['observation']['enemies'].append({'id':'SECOND','combat_id':2,'hp':'100'})
        a['terminal_combat']=copy.deepcopy(a['terminal_combat'])
        a['terminal_combat']['enemies']=[{'id':'BOSS','combat_id':1,'hp':'200'},{'id':'SECOND','combat_id':2,'hp':'50'}]
        p=combat_loss_progress(a)
        self.assertEqual(p['revivals_observed'],1);self.assertEqual(p['observed_hp_removed'],150)
        self.assertAlmostEqual(p['hp_removed_fraction'],150/400)
    def test_hoarded_gold_and_potions_do_not_make_death_better(self):
        a=death();b=copy.deepcopy(a);b['observation'].update(gold=10000,potions=['ANY']*3)
        self.assertEqual(utility(a),utility(b))
    def test_actual_progress_precedes_survival_turns(self):
        self.assertGreater(utility(death(20,2)),utility(death(80,30)))
        self.assertGreater(utility(death(20,5)),utility(death(20,2)))
    def test_native_terminal_effects_override_pre_action_proxy(self):
        a=death(80);a['terminal_combat']=copy.deepcopy(a['terminal_combat'])
        a['terminal_combat']['enemies'][0]['hp']='10'
        self.assertAlmostEqual(combat_loss_progress(a)['hp_removed_fraction'],.9)
    def test_missing_terminal_snapshot_is_explicitly_a_proxy(self):
        a=death();a.pop('terminal_combat');m=combat_loss_progress(a)
        self.assertFalse(m['terminal_snapshot_available']);self.assertEqual(m['source'],'last_observed_trace')
    def test_disappearing_enemy_is_not_invented_as_a_kill(self):
        a=death(100);a['decision_evidence'][-1]['observation']['enemies']=[]
        self.assertEqual(combat_loss_progress(a)['hp_removed_fraction'],0)
    def test_noncombat_death_does_not_reuse_old_fight_progress(self):
        a=death();a.pop('terminal_combat')
        a['decision_evidence'].append({'phase':'event','observation':{'act':1,'floor':33,'turn':None,'enemies':None}})
        self.assertFalse(combat_loss_progress(a)['available'])
    def test_new_fight_on_same_floor_resets_observations(self):
        a=death(10);a.pop('terminal_combat')
        a['decision_evidence'].append({'phase':'combat','observation':{'act':1,'floor':33,'turn':1,'enemies':[{'id':'OTHER','combat_id':1,'hp':'500'}]}})
        self.assertEqual(combat_loss_progress(a)['hp_removed_fraction'],0)

if __name__=='__main__':unittest.main()
