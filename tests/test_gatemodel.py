"""Per-seed gate model: allocation statistics from this run's own native boss fights."""
import unittest
from collections import Counter
from spire_exact.planning.gatemodel import GateModels,boss_fights,boss_fight_rows,entry_features,item_delta,aggregate_features,_ols,SIZE_CLAMP
from spire_exact.planning.gates import PRESETS,preset
from spire_exact.planning.repairs import RepairQueue
from spire_exact.planning.pool import runtime_settings,apply_heap_limit,LARGE_GEN0_BYTES
from types import SimpleNamespace

def card(name,upgrade=0):return {'id':name,'upgrade':upgrade}

def rollout(cards,won,*,tag=0,act=0,floor=17,hp_after=40,enemy_left=60,relics=(),menu=None,then=None):
    """One boss fight after one labelled decision. `won` -> the route dies later at a hallway fight."""
    deck=[card(c)if isinstance(c,str)else c for c in cards]
    before={'act':act,'floor':floor-1,'room':None,'hp':'60','max_hp':'80','deck':deck[:-1],'relics':[],'potions':[None,None]}
    entry={'act':act,'floor':floor,'room':'Boss','turn':1,'hp':'60','max_hp':'80','deck':deck,'relics':list(relics),
           'potions':[None,None],'enemies':[{'id':'BOSS','combat_id':1,'hp':'100'}]}
    chosen={'kind':'card_reward','index':0,'tag':tag}
    first={'phase':'card_reward','observation':before,'available_actions':[chosen,{'kind':'card_skip'}],
           'option_labels':menu or[['card:'+deck[-1]['id']],['card_skip']]}
    trace=[chosen,{'kind':'end_turn'}];evidence=[first,{'phase':'combat','observation':entry,'available_actions':[{'kind':'end_turn'}]}]
    if not won:
        return {'status':'TERMINAL','value':[0],'reason':None,'trace':trace,'decision_evidence':evidence,
                'observation':{'act':act,'floor':floor,'room':'Boss','hp':'0','max_hp':'80'},
                'terminal_combat':{'act':act,'floor':floor,'turn':4,'enemies':[{'id':'BOSS','combat_id':1,'hp':str(enemy_left),'max_hp':'100'}]}}
    after=dict(entry,room=None,turn=None,enemies=None,hp=str(hp_after))
    trace.append({'kind':'reward','index':0});evidence.append({'phase':'rewards','observation':after,'available_actions':[{'kind':'reward','index':0}]})
    if then is not None:return then(trace,evidence,after)
    return {'status':'TERMINAL','value':[0],'reason':None,'trace':trace,'decision_evidence':evidence,
            'observation':{'act':act,'floor':floor+3,'room':'Monster','hp':'0','max_hp':'80'}}

def staged(cards,hps,tag=0):
    """A lost boss fight whose single enemy shows the given HP at successive turns; a rise after 0 is a new form."""
    deck=[card(c)for c in cards]
    def obs(turn,hp):return {'act':2,'floor':49,'room':'Boss','turn':turn,'hp':'30','max_hp':'80','deck':deck,'relics':[],'potions':[None,None],
                             'enemies':[{'id':'BOSS','combat_id':1,'hp':str(hp)}]}
    chosen={'kind':'card_reward','index':0,'tag':tag}
    trace=[chosen];evidence=[{'phase':'card_reward','observation':{'act':2,'floor':48,'hp':'30','max_hp':'80','deck':deck[:-1],'relics':[],'potions':[None,None]},
                             'available_actions':[chosen],'option_labels':[['card:'+deck[-1]['id']]]}]
    for turn,hp in enumerate(hps[:-1],1):
        trace.append({'kind':'end_turn'});evidence.append({'phase':'combat','observation':obs(turn,hp),'available_actions':[{'kind':'end_turn'}]})
    return {'status':'TERMINAL','value':[0],'reason':None,'trace':trace,'decision_evidence':evidence,
            'observation':{'act':2,'floor':49,'room':'Boss','hp':'0','max_hp':'80'},
            'terminal_combat':{'act':2,'floor':49,'turn':len(hps),'enemies':[{'id':'BOSS','combat_id':1,'hp':str(hps[-1]),'max_hp':'100'}]}}

def fitted(seed=7,**options):
    """Thirty entries where GOOD wins and BAD loses, both on top of the same starter deck."""
    m=GateModels(seed,minimum=8,refresh=4,**options)
    for i in range(15):
        m.add(rollout(['STRIKE','DEFEND','GOOD'],True,tag=2*i,hp_after=40+i%3),f'w{i}')
        m.add(rollout(['STRIKE','DEFEND','BAD'],False,tag=2*i+1,enemy_left=70+i%3),f'l{i}')
    m.refit();return m

class Extraction(unittest.TestCase):
    def test_lost_boss_fight_scores_observed_enemy_hp_removed(self):
        (gate,index,entry,outcome),=boss_fights(rollout(['A'],False,enemy_left=25))
        self.assertEqual((gate,index),((0,0),1));self.assertAlmostEqual(outcome,.75)
    def test_survived_boss_fight_scores_hp_kept_even_when_the_route_dies_later(self):
        (_,_,_,outcome),=boss_fights(rollout(['A'],True,hp_after=20))
        self.assertAlmostEqual(outcome,1.25)
    def test_request_stopped_right_after_the_fight_still_counts(self):
        r=rollout(['A'],True);r['trace']=r['trace'][:2];r['decision_evidence']=r['decision_evidence'][:2]
        r.update(status='BUDGET',value=None,reason='candidate_horizon',observation={'act':0,'floor':17,'hp':'30','max_hp':'80','turn':None})
        self.assertAlmostEqual(boss_fights(r)[0][3],1.375)
    def test_interrupted_or_invalid_execution_yields_no_outcome(self):
        r=rollout(['A'],False);r.update(status='UNKNOWN',reason='NATIVE_TASK_TIMEOUT',value=None)
        self.assertEqual(boss_fights(r),[])
        r=rollout(['A'],True);r['trace']=r['trace'][:2];r['decision_evidence']=r['decision_evidence'][:2]
        r.update(status='BUDGET',value=None,reason='candidate_decision_budget',observation={'act':0,'floor':17,'hp':'30','max_hp':'80','turn':3})
        self.assertEqual(boss_fights(r),[])
    def test_second_boss_of_an_act_is_a_separate_gate(self):
        def second(trace,evidence,after):
            entry=dict(evidence[1]['observation'],floor=49)
            trace.append({'kind':'end_turn'});evidence.append({'phase':'combat','observation':entry,'available_actions':[{'kind':'end_turn'}]})
            return {'status':'TERMINAL','value':[0],'reason':None,'trace':trace,'decision_evidence':evidence,
                    'observation':{'act':2,'floor':49,'room':'Boss','hp':'0','max_hp':'80'},
                    'terminal_combat':{'act':2,'floor':49,'turn':2,'enemies':[{'id':'BOSS','combat_id':1,'hp':'90','max_hp':'100'}]}}
        gates=[g for g,_,_,_ in boss_fights(rollout(['A'],True,act=2,floor=48,then=second))]
        self.assertEqual(gates,[(2,0),(2,1)])
    def test_a_lost_fight_reports_the_hp_removed_and_every_life_it_saw(self):
        row,=boss_fight_rows(staged(['A'],[100,40]));self.assertEqual(row['lost'],(60.0,100.0));self.assertAlmostEqual(row['outcome'],.6)
        row,=boss_fight_rows(staged(['A'],[100,0,200,150]));self.assertEqual(row['lost'],(150.0,300.0));self.assertAlmostEqual(row['outcome'],.5)
        self.assertIsNone(boss_fight_rows(rollout(['A'],True))[0]['lost'])
    def test_features_and_deltas_use_native_ids(self):
        f=entry_features({'deck':[card('A'),card('A',1)],'relics':['R'],'potions':['P',None],'hp':'40','max_hp':'80'})
        self.assertEqual((f['card:A'],f['up:A'],f['relic:R'],f['potion:P'],f['#hp'],f['#deck']),(2,1,1,1,.5,.2))
        d=item_delta({'deck':[card('A')],'relics':[]},{'deck':[card('A'),card('CURSE')],'relics':['R'],'potions':['P']})
        self.assertEqual(d,{'card:CURSE':1,'relic:R':1})

class Model(unittest.TestCase):
    def test_item_that_wins_is_valued_and_item_that_loses_is_not(self):
        m=fitted()
        self.assertGreater(m.z('card:GOOD',0),2);self.assertLess(m.z('card:BAD',0),-2)
        self.assertEqual(m.z('card:NEVER_SEEN',0),0)
        self.assertEqual(m.z('remove:BAD',0),-m.z('card:BAD',0))
        self.assertEqual(m.z('card:GOOD',1),0)        # no gate fitted at or after act 1
    def test_prediction_orders_entries(self):
        m=fitted();good=rollout(['STRIKE','DEFEND','GOOD'],True)['decision_evidence'][1]['observation']
        bad=rollout(['STRIKE','DEFEND','BAD'],False)['decision_evidence'][1]['observation']
        self.assertGreater(m.predict((0,0),good),m.predict((0,0),bad))
        self.assertIsNone(m.predict((1,0),good))
    def test_prior_is_reproducible_and_depends_on_seed_and_serial(self):
        a,b=fitted(7),fitted(7)
        self.assertEqual([a.prior(s)for s in range(20)],[b.prior(s)for s in range(20)])
        self.assertNotEqual([a.prior(s)for s in range(40)],[fitted(8).prior(s)for s in range(40)])
    def test_strong_evidence_is_applied_without_noise_and_as_a_tier_per_act(self):
        m=fitted(noise=0.0);p=m.prior(1)
        self.assertGreaterEqual(p['card:GOOD'][0],1);self.assertLessEqual(p['card:BAD'][0],-1)
        self.assertEqual(p['remove:BAD'][0],-p['card:BAD'][0])
        self.assertTrue(all(type(t)is int and -2<=t<=2 for row in p.values()for t in row))
    def test_skips_potions_routes_and_rest_never_receive_tiers(self):
        m=fitted(noise=3.0)
        for serial in range(50):
            for label in m.prior(serial):
                self.assertNotIn(label.split(':')[0],('potion','use_potion','go','rest'))
                self.assertNotIn(label,('card_skip','treasure_skip','rewards_skip','buy_removal'))
    def test_no_model_before_enough_entries(self):
        m=GateModels(minimum=8,refresh=4)
        for i in range(5):m.add(rollout(['GOOD'],True,tag=i),f'r{i}')
        self.assertEqual(m.prior(0),{});self.assertEqual(m.z('card:GOOD',0),0)
    def test_same_entry_is_counted_once_and_keeps_its_best_outcome(self):
        m=GateModels(minimum=8,refresh=4)
        m.add(rollout(['A'],False,enemy_left=80),'first');m.add(rollout(['A'],False,enemy_left=80),'repeat')
        gate=m.gates[(0,0)];self.assertEqual(len(gate.entries),1)
        m.add(rollout(['A'],True,hp_after=40),'retry')       # same entry, stronger tactical plan
        self.assertEqual(len(gate.entries),1);self.assertAlmostEqual(next(iter(gate.entries.values()))[1],1.5)
    def test_fights_against_a_boss_with_several_forms_share_one_scale(self):
        m=GateModels(minimum=4,refresh=2)
        m.add(staged(['A'],[100,10],tag=0),'first-form')                    # 90 of the 100 HP it ever saw
        gate=m.gates[(2,0)];first=next(iter(gate.entries.values()))
        self.assertAlmostEqual(first[1],.9);self.assertEqual(gate.total,100)
        m.add(staged(['B'],[100,0,200,150],tag=1),'second-form')            # the whole first form plus 50 more
        self.assertEqual(gate.total,300)
        m.add(staged(['C'],[100,60],tag=2),'c');m.add(staged(['D'],[100,0,200,0,300,290],tag=3),'third-form')
        m.refit()
        outcome={label:m.sources[label][(2,0)][1]for label in('first-form','second-form','c','third-form')}
        self.assertEqual(gate.total,600)
        self.assertAlmostEqual(outcome['first-form'],90/600);self.assertAlmostEqual(outcome['second-form'],150/600)
        self.assertAlmostEqual(outcome['third-form'],310/600)
        self.assertLess(outcome['c'],outcome['first-form']);self.assertLess(outcome['first-form'],outcome['second-form'])
        self.assertLess(outcome['second-form'],outcome['third-form'])       # deeper is always better, never the reverse
        self.assertAlmostEqual(m.snapshot()['gates'][0]['best_outcome'],310/600)
    def test_a_survived_retry_replaces_a_loss_and_a_better_loss_replaces_a_worse_one(self):
        m=GateModels(minimum=8,refresh=4)
        m.add(rollout(['A'],False,enemy_left=80),'first');gate=m.gates[(0,0)];row=next(iter(gate.entries.values()))
        self.assertEqual(row[2],20.0)
        m.add(rollout(['A'],False,enemy_left=90),'worse');self.assertEqual(row[2],20.0)
        m.add(rollout(['A'],False,enemy_left=30),'better');self.assertEqual(row[2],70.0);self.assertAlmostEqual(row[1],.7)
        m.add(rollout(['A'],True,hp_after=40),'won');self.assertIsNone(row[2]);self.assertAlmostEqual(row[1],1.5)
        m.add(rollout(['A'],False,enemy_left=10),'late-loss');self.assertIsNone(row[2]);self.assertAlmostEqual(row[1],1.5)
    def test_parent_child_pairs_feed_the_difference_model(self):
        m=GateModels(minimum=8,refresh=4)
        m.add(rollout(['STRIKE','BAD'],False,tag=0),'parent')
        for i in range(12):
            m.add(rollout(['STRIKE','GOOD'],True,tag=i+1,hp_after=30+i),f'c{i}','parent')
        m.refit();gate=m.gates[(0,0)]
        self.assertEqual(len(gate.pairs),12);self.assertEqual(gate.pairs_fitted,12)
        self.assertGreater(gate.pair_z['card:GOOD'],0);self.assertLess(gate.pair_z['card:BAD'],0)
        self.assertEqual(m.snapshot()['gates'][0]['fitted_pairs'],12)
    def test_event_option_is_valued_by_the_items_it_was_observed_to_grant(self):
        m=GateModels(7,minimum=8,refresh=4,noise=0.0)
        menu=[['event:TAKE_CURSE'],['card_skip']]
        for i in range(15):
            m.add(rollout(['STRIKE','DEFEND','GOOD'],True,tag=2*i),f'w{i}')
            m.add(rollout(['STRIKE','DEFEND','BAD'],False,tag=2*i+1,menu=menu),f'l{i}')
        m.refit()
        self.assertEqual(m.deltas['event:TAKE_CURSE'],{'card:BAD':1})
        self.assertLess(m.z('event:TAKE_CURSE',0),-2);self.assertLessEqual(m.prior(3)['event:TAKE_CURSE'][0],-1)
    def test_least_squares_separates_correlated_columns_and_drops_constant_ones(self):
        rows=[({'size':s/10,'hp':h/10,'flat':1.0},1.0-0.2*s/10+0.5*h/10)for s in range(10,30)for h in (4,7,10)]
        weights,z=_ols(rows,('size','hp','flat','absent'))
        self.assertAlmostEqual(weights['size'],-0.2,places=6);self.assertAlmostEqual(weights['hp'],0.5,places=6)
        self.assertNotIn('flat',weights);self.assertNotIn('absent',weights)
        self.assertLess(z['size'],-2);self.assertGreater(z['hp'],2)
        self.assertEqual(_ols(rows[:3],('size','hp')),({},{}))                # too few rows for the columns
        same=[({'size':x,'hp':x},float(i%2))for i,x in enumerate((1.0,2.0,3.0,4.0,5.0,6.0,7.0,8.0))]
        self.assertEqual(_ols(same,('size','hp')),({},{}))                    # collinear: no estimate, no crash
        self.assertEqual(aggregate_features({'#deck':2.5,'#hp':.5,'#potions':2.0,'up:A':2,'up:B':1,'relic:R':1,'card:A':3}),
                         {'size':2.5,'hp':.5,'potions':2.0,'upgrades':.3,'relics':.1})
    def sized(self,slope,**options):
        """Forty entries whose outcome moves with deck size only (filler cards differ in every entry)."""
        m=GateModels(7,minimum=8,refresh=4,**options)
        for i in range(40):
            extra=i%8
            won=1.2+slope*extra>=1.0
            cards=['STRIKE','DEFEND']+[f'FILLER{i}_{j}'for j in range(extra)]
            m.add(rollout(cards,won,tag=i,hp_after=max(1,int(80*(0.2+slope*extra))),enemy_left=max(0,int(100*(1-(1.2+slope*extra))))),f'e{i}')
        m.refit();return m
    def test_deck_size_prior_is_off_unless_requested(self):
        m=self.sized(-0.12)
        self.assertEqual(m.z('card_skip',0),0);self.assertNotIn('card_skip',m.labels())
        self.assertTrue(all('card_skip'not in m.prior(s)and'buy_removal'not in m.prior(s)for s in range(30)))
    def test_deck_size_prior_values_skipping_when_larger_decks_do_worse(self):
        m=self.sized(-0.12,size=True,noise=0.0)
        self.assertLess(m.gates[(0,0)].size_z,-2)
        self.assertEqual(m.z('card_skip',0),SIZE_CLAMP);self.assertEqual(m.z('buy_removal',0),SIZE_CLAMP)   # clamped: one tier
        self.assertEqual(m.prior(0)['card_skip'],[1]);self.assertEqual(m.prior(0)['buy_removal'],[1])
        self.assertEqual(m.snapshot()['gates'][0]['size_z'],round(m.gates[(0,0)].size_z,2))
    def test_deck_size_prior_discourages_skipping_when_larger_decks_do_better(self):
        m=self.sized(0.12,size=True,noise=0.0)
        self.assertGreater(m.gates[(0,0)].size_z,2);self.assertEqual(m.prior(0)['card_skip'],[-1])
    def test_snapshot_reports_allocation_scope(self):
        s=fitted().snapshot()
        self.assertEqual(s['gates'][0]['encounter'],'BOSS');self.assertEqual(s['gates'][0]['survived_entries'],15)
        self.assertIn('never a bound',s['scope'])

class Plumbing(unittest.TestCase):
    def test_gate_repairs_are_best_first_with_stable_ties(self):
        q=RepairQueue('gate')
        for name,priority in(('a',(0,0,1,17)),('b',(0,0,2,48)),('c',(0,0,2,48)),('d',(0,0,2,33))):q.append({'name':name,'priority':priority})
        self.assertEqual([q.popleft()['name']for _ in range(4)],['b','c','d','a'])
    def test_presets_are_plain_request_data_and_isolated_copies(self):
        self.assertEqual(preset('none'),{'plans':{},'retries':()})
        a=preset('escalate');a['plans']['Boss']['members'].clear()
        self.assertEqual(len(preset('escalate')['plans']['Boss']['members']),4)
        self.assertEqual(PRESETS['escalate']['plans']['Boss']['members'][0]['beam'],45)   # first member = historical search
        for patch in preset('escalate')['retries']:self.assertEqual(set(patch['gate_plans']),{'Boss','Elite','Monster'})
        with self.assertRaises(ValueError):preset('missing')
    def test_final_act_plan_is_the_boss_plan_without_the_escalation_rule(self):
        gates=preset('escalate');boss=gates['plans']['Boss'];final=gates['final']['open']
        self.assertEqual(final['members'],boss['members']);self.assertEqual(final['select'],boss['select'])
        self.assertIn('escalate_percent',boss);self.assertNotIn('escalate_percent',final)
        # The plan is off by default and retries replace every plan of a request, the final one included.
        self.assertNotIn('FinalBoss',gates['plans'])
        for patch in gates['retries']:self.assertNotIn('FinalBoss',patch['gate_plans'])
    def test_large_gen0_profile_only_adds_the_gen0_budget(self):
        plan=SimpleNamespace(dop=1,effective_cpus=8,worker_memory_bytes=1792*1024**2)
        runtime=runtime_settings(plan,'server-large-gen0')
        self.assertTrue(runtime['server_gc']);self.assertEqual((runtime['processors'],runtime['gc_heaps'],runtime['solver_dop']),(2,1,1))
        env=apply_heap_limit({'COMPlus_GCgen0size':'1','OTHER':'keep'},runtime)
        self.assertEqual(int(env['DOTNET_GCgen0size'],16),LARGE_GEN0_BYTES);self.assertNotIn('COMPlus_GCgen0size',env)
        self.assertNotIn('DOTNET_GCHeapHardLimit',env);self.assertEqual(env['OTHER'],'keep')
        self.assertNotIn('DOTNET_GCgen0size',apply_heap_limit({},runtime_settings(plan,'server-one-heap')))

def lost(removed,total=100.0):return {'won':False,'outcome':None,'lost':(float(removed),float(total)),'nodes':1,'search_seconds':.1}
def kept(hp,max_hp=80.0):return {'won':True,'outcome':1+hp/max_hp,'lost':None,'nodes':1,'search_seconds':.1}

def table(base,deltas,filler=5):
    """Probe rows of one table: a lost base, the given removed-HP changes, and `filler` edits that change nothing."""
    rows=[(None,lost(base))]+[(label,lost(base+d))for label,d in deltas.items()]
    return rows+[(f'card:FILLER{i}',lost(base))for i in range(filler)]

class ProbeTables(unittest.TestCase):
    """Synthetic probe tables are a separate, switchable source of label values."""
    def test_probe_values_are_deltas_in_units_of_the_table_spread(self):
        m=GateModels(probes=True)
        self.assertEqual(m.add_probes((0,0),0,table(40,{'card:GOOD':30,'card:BAD':-10,'remove:STRIKE':5})),9)
        z=m.gates[(0,0)].probe_z
        # Most edits change nothing: the spread is the floor (0.05 of the boss), so +0.30 reads 6.
        self.assertAlmostEqual(z['card:GOOD'],6.0);self.assertAlmostEqual(z['card:BAD'],-2.0);self.assertAlmostEqual(z['remove:STRIKE'],1.0)
        self.assertAlmostEqual(m.z('card:GOOD',0),6.0);self.assertEqual(m.z('card:GOOD',1),0.0)      # only gates at or after the act
        self.assertEqual(m.z('card:FILLER0',0),0.0)
    def test_a_noisy_table_scales_its_own_deltas_down(self):
        m=GateModels(probes=True)
        noisy={f'card:N{i}':d for i,d in enumerate((-20,-15,-10,10,15,20))}
        m.add_probes((0,0),0,table(40,{'card:GOOD':30,**noisy},filler=0))
        # deltas -.20 -.15 -.10 .10 .15 .20 .30: median .10, median absolute deviation .20 -> spread 0.297
        self.assertAlmostEqual(m.gates[(0,0)].probe_z['card:GOOD'],0.30/(1.4826*0.20),places=6)
    def test_table_is_read_only_with_its_base_and_enough_edits(self):
        m=GateModels(probes=True)
        m.add_probes((0,0),0,table(40,{'card:GOOD':30},filler=4)[1:])           # no base yet
        self.assertEqual(m.gates[(0,0)].probe_z,{})
        m.add_probes((0,0),0,[(None,lost(40))])                                 # base arrives in a later batch
        self.assertEqual(m.gates[(0,0)].probe_z,{})                             # still only five edits
        m.add_probes((0,0),0,[('card:MORE',lost(40)),('card:FAILED',None)])     # unfinished probes are skipped
        self.assertAlmostEqual(m.gates[(0,0)].probe_z['card:GOOD'],6.0);self.assertNotIn('card:FAILED',m.gates[(0,0)].probe_z)
    def test_switch_off_ignores_tables_everywhere(self):
        m=GateModels()
        m.add_probes((0,0),0,table(40,{'card:GOOD':30}))
        self.assertEqual(m.z('card:GOOD',0),0.0);self.assertEqual(m.prior(3),{});self.assertNotIn('card:GOOD',m.labels())
        self.assertNotIn('probe_tables',m.snapshot()['gates'][0])
    def test_probe_tiers_reach_the_prior_before_any_regression_is_fitted(self):
        m=GateModels(11,probes=True)
        m.add_probes((1,0),0,table(40,{'card:GOOD':30,'card:BAD':-30}))
        tiers=[m.prior(serial)for serial in range(40)]
        self.assertTrue(all(len(t['card:GOOD'])==2 and t['card:GOOD'][0]>=1 for t in tiers))     # acts 0 and 1 lead to this gate
        self.assertTrue(all(t['card:BAD'][0]<=-1 for t in tiers));self.assertIn(2,{t['card:GOOD'][0]for t in tiers})
        self.assertEqual(m.snapshot()['gates'][0]['probe_top'][0][0],'card:GOOD')
    def test_probe_and_regression_are_averaged_when_both_speak(self):
        m=fitted(probes=True);gate=m.gates[(0,0)]
        learned=m._label_at(gate,'card:GOOD');self.assertGreater(learned,0)
        m.add_probes((0,0),0,table(40,{'card:GOOD':-30,'card:NEW':30}))
        self.assertAlmostEqual(m.z('card:GOOD',0),0.5*(learned-6.0))
        self.assertAlmostEqual(m.z('card:NEW',0),6.0)                            # the regression knows nothing about it
        self.assertAlmostEqual(fitted().z('card:GOOD',0),learned)                # switch off: unchanged
    def test_tables_of_one_gate_add_up(self):
        m=GateModels(probes=True)
        m.add_probes((0,0),0,table(40,{'card:GOOD':30}));m.add_probes((0,0),1,table(50,{'card:GOOD':20,'card:OTHER':10}))
        z=m.gates[(0,0)].probe_z
        self.assertAlmostEqual(z['card:GOOD'],(6.0+4.0)/2**.5);self.assertAlmostEqual(z['card:OTHER'],2.0)
    def test_probes_share_the_gate_scale_but_never_move_it(self):
        m=GateModels(probes=True)
        m.add(staged(['A','B'],[111,0,212,150]),'real')                          # real fights saw 111 + 212 = 323
        self.assertEqual(m.gates[(2,0)].total,323.0)
        rows=[(None,lost(160,323)),('card:THIRD',lost(480,636))]+[(f'card:F{i}',lost(160,323))for i in range(5)]
        m.add_probes((2,0),0,rows)
        self.assertEqual(m.gates[(2,0)].total,323.0)                             # the real scale is untouched
        # the probe that reached a third form sets the probe scale: (480 - 160) / 636
        self.assertAlmostEqual(m.gates[(2,0)].probe_z['card:THIRD'],(480-160)/636/0.05)
        self.assertEqual(len(m.gates[(2,0)].entries),1)                          # probes are not entries
    def test_a_survived_probe_counts_above_every_lost_one(self):
        m=GateModels(probes=True)
        m.add_probes((0,0),0,[(None,lost(90)),('card:WIN',kept(40))]+[(f'card:F{i}',lost(90))for i in range(5)])
        self.assertAlmostEqual(m.gates[(0,0)].probe_z['card:WIN'],(1.5-0.9)/0.05)

BLOCK=['STRIKE']*4+['DEFEND']*4+['IRON_WAVE','SHRUG'];POWER=['STRIKE','DEFEND','INFLAME','INFLAME','DEMON','HEAVY','TWIN','TWIN','LIMIT','BASH']

class FamilyRecord(unittest.TestCase):
    """A deck family's record at a gate: every real entry with an alike deck, normalised by attempts."""
    def model(self):
        m=GateModels(7)
        for i in range(5):m.add(rollout(BLOCK+['X%d'%i],False,tag=i),f'b{i}')
        for i in range(3):m.add(rollout(POWER+['Y%d'%i],i>0,tag=10+i),f'p{i}')
        return m
    def test_record_counts_alike_entries_and_their_passes(self):
        m=self.model()
        self.assertEqual(m.family((0,0),Counter(BLOCK)),(5,0));self.assertEqual(m.family((0,0),Counter(POWER)),(3,2))
        self.assertEqual(m.family((0,0),Counter(['CLASH']*10)),(0,0));self.assertEqual(m.family((2,1),Counter(BLOCK)),(0,0))
        self.assertEqual(m.family((0,0),Counter(BLOCK),100),(0,0))                  # no entry has exactly this deck
    def test_memo_scans_only_new_entries_and_sees_a_later_pass_of_a_known_entry(self):
        m=self.model();memo=[0,[]]
        self.assertEqual(m.family((0,0),Counter(BLOCK),75,memo),(5,0));self.assertEqual(memo[0],8)
        m.add(rollout(BLOCK+['X9'],False,tag=30),'b9');m.add(rollout(BLOCK+['X0'],True,tag=0),'b0-retry')
        self.assertEqual(m.family((0,0),Counter(BLOCK),75,memo),(6,1));self.assertEqual(memo[0],9)
        self.assertEqual(m.family((0,0),Counter(BLOCK)),(6,1))
    def test_probes_never_enter_the_record(self):
        m=self.model();m.add_probes((0,0),0,[(None,{'won':True,'outcome':1.5,'lost':None})])
        self.assertEqual(m.family((0,0),Counter(BLOCK)),(5,0))
    def test_optimistic_rate_falls_with_failures_and_never_reaches_zero(self):
        rate=GateModels.optimistic
        self.assertAlmostEqual(rate(0,0),0.5+(0.25/3)**0.5);self.assertAlmostEqual(rate(8,0),0.1+(0.09/11)**0.5)
        self.assertGreater(rate(8,3),rate(0,0)*0.5);self.assertGreater(rate(8,3),rate(8,0))
        self.assertGreater(rate(8,0),rate(30,0));self.assertGreater(rate(30,0),rate(125,0));self.assertGreater(rate(125,0),0.0)
        self.assertLess(rate(125,0)*30,rate(0,0))
    def test_novelty_is_one_for_unseen_items_and_falls_with_evidence(self):
        m=fitted()
        self.assertEqual(m.novelty('card:NEVER',0),1.0);self.assertAlmostEqual(m.novelty('card:GOOD',0),(3.0/(7.5+3.0))**0.5)
        self.assertEqual(m.novelty('remove:GOOD',0),m.novelty('card:GOOD',0));self.assertEqual(m.novelty('upgrade:NEVER',0),1.0)
        self.assertEqual(m.novelty('card:STRIKE',0),1.0)                             # every entry owns it alike: nothing was learned
        for label in('go:Elite','rest:heal','potion:FIRE','card_skip','rewards_skip','event:unknown'):self.assertEqual(m.novelty(label,0),0.0)
        self.assertEqual(m.novelty('card:NEVER',1),0.0);self.assertEqual(GateModels(7).novelty('card:NEVER',0),0.0)
    def test_optimism_adds_novelty_to_the_option_value_only_when_asked(self):
        m=fitted()
        self.assertEqual(m.best(['card:NEVER'],0),0.0);self.assertEqual(m.best(['card:NEVER'],0,1.0),1.0)
        self.assertAlmostEqual(m.best(['card:GOOD'],0,0.5),m.z('card:GOOD',0)+0.5*m.novelty('card:GOOD',0))
        self.assertEqual(m.best(['go:Elite'],0,1.0),0.0);self.assertEqual(m.best([],0,1.0),0.0)
    def test_record_and_novelty_do_not_change_values_or_tiers(self):
        a,b=fitted(),fitted()
        b.family((0,0),Counter(['STRIKE','DEFEND','GOOD']));b.novelty('card:GOOD',0);b.best(['card:BAD'],0,1.0)
        self.assertEqual(a.prior(5),b.prior(5));self.assertEqual(a.snapshot(),b.snapshot())

if __name__=='__main__':unittest.main()
