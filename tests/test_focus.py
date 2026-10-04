"""Elite-guided scheduling: allocation only, exact prefixes, nothing proposed twice."""
import copy,json,unittest
from spire_exact.canonical import canonical
from spire_exact.planning.focus import FocusScheduler
from spire_exact.planning.gatemodel import GateModels
from spire_exact.planning.indexed_strategy import IndexedStrategicScheduler

def trajectory(floors=33,tag=0,options=3,labels=True,death_hp=50,deck=None):
    """A death on `floors`; one map and one card decision per floor, then the fatal fight.
    `deck` (card ids) is the final deck, only read by the deck-family cap."""
    evidence=[];trace=[]
    for floor in range(1,floors+1):
        for phase,prefix in(('map','go'),('card_reward','card')):
            actions=[{'kind':phase,'index':i,'tag':tag if i==0 else 0}for i in range(options)]
            trace.append(actions[0])
            row={'phase':phase,'observation':{'floor':floor,'act':int(floor>17)},'available_actions':actions}
            if labels:row['option_labels']=[[f'{prefix}:{floor}:{i}']for i in range(options)]
            evidence.append(row)
    enemy=lambda hp:{'id':'BOSS','combat_id':1,'hp':str(hp)}
    start={'act':int(floors>17),'floor':floors,'turn':1,'room':'Boss','enemies':[enemy(100)]}
    end={'act':int(floors>17),'floor':floors,'turn':5,'room':'Boss','enemies':[enemy(death_hp)]}
    trace.append({'kind':'end_turn'});evidence.append({'phase':'combat','observation':start,'available_actions':[{'kind':'end_turn'}]})
    return {'status':'TERMINAL','value':[0],'trace':trace,'decision_evidence':evidence,'terminal_combat':end,
            'observation':{'floor':floors,'act':int(floors>17),'room':'Boss','hp':'0','max_hp':'80',
                           **({'deck':[{'id':c,'upgrade':0}for c in deck]}if deck is not None else{})}}

# Two deck families: eight shared cards plus two of their own, so decks of one family are alike
# (Jaccard 8 / 12 or more) and decks of different families are not.
def blocky(extra=()):return ['STRIKE']*4+['DEFEND']*4+['IRON_WAVE','SHRUG']+list(extra)
def strength(extra=()):return ['STRIKE','DEFEND','INFLAME','INFLAME','DEMON','HEAVY','TWIN','TWIN','LIMIT','BASH']+list(extra)

class Values:
    """Stand-in gate model: a fixed value per label."""
    def __init__(self,values):self.values=values
    def best(self,names,act):return max((self.values.get(n,0.0)for n in names),default=0.0)

class FocusTests(unittest.TestCase):
    def test_first_focus_proposals_are_next_to_the_fatal_floor(self):
        s=FocusScheduler(explore=0);s.add(trajectory(),'root')
        floors=[s.next()['floor']for _ in range(6)]
        self.assertEqual(floors[0],33);self.assertTrue(all(f>=31 for f in floors))
        # Widening a late decision and walking back are interleaved.
        self.assertEqual(floors,[33,33,32,32,33,33])
    def test_every_proposal_is_an_exact_prefix_plus_a_legal_untried_alternative(self):
        r=trajectory();s=FocusScheduler();s.add(r,'root');seen=set()
        for _ in range(60):
            p=s.next();i=p['index'];key=canonical(p['prefix'])
            self.assertNotIn(key,seen);seen.add(key)
            self.assertEqual(p['prefix'][:-1],r['trace'][:i])
            self.assertIn(p['prefix'][-1],r['decision_evidence'][i]['available_actions'])
            self.assertNotEqual(p['prefix'][-1],r['trace'][i])
    def test_focus_and_explorer_share_three_to_one_and_never_repeat_each_other(self):
        s=FocusScheduler();s.add(trajectory(),'root')
        picks=[s.next()for _ in range(40)]
        focus=sum(p['category']=='focus'for p in picks)
        self.assertEqual(focus,30);self.assertEqual(len({canonical(p['prefix'])for p in picks}),40)
        self.assertEqual(s.snapshot()['focus_evaluations'],30);self.assertEqual(s.snapshot()['explorer_evaluations'],10)
    def test_everything_is_eventually_proposed_then_none(self):
        r=trajectory(floors=3);s=FocusScheduler();s.add(r,'root');count=0
        while s.next()is not None:count+=1
        self.assertEqual(count,3*2*2)
        self.assertIsNone(s.next())
    def test_better_trajectory_takes_the_largest_focus_share(self):
        s=FocusScheduler(explore=0);s.add(trajectory(death_hp=80),'weak')
        s.add(trajectory(tag=1,death_hp=10),'strong')
        sources=[s.next()['source']for _ in range(30)]
        self.assertGreater(sources.count('strong'),sources.count('weak'))
        self.assertGreater(sources.count('weak'),0)
        self.assertEqual(s.snapshot()['elites'][0]['label'],'strong')
    def test_equal_outcome_does_not_add_a_parallel_copy(self):
        s=FocusScheduler();s.add(trajectory(),'first');s.add(trajectory(tag=1),'same-outcome')
        self.assertEqual(s.snapshot()['elite_pool'],1)
        self.assertEqual({s.next()['source']for _ in range(9)}-{'first','same-outcome'},set())
    def test_learned_values_order_options_without_removing_any(self):
        values=Values({'card:33:2':3.0,'card:33:1':-3.0})
        s=FocusScheduler(models=values,explore=0);s.add(trajectory(),'root')
        first=s.next()
        self.assertEqual((first['phase'],first['prefix'][-1]['index']),('card_reward',2))
        rest=[s.next()for _ in range(130)]
        self.assertIn(('card_reward',33,1),[(p['phase'],p['floor'],p['prefix'][-1]['index'])for p in rest])
    def test_same_inputs_give_the_same_order(self):
        def run():
            s=FocusScheduler(models=Values({'card:30:1':2.5}));s.add(trajectory(),'a');out=[]
            for i in range(25):
                p=s.next();out.append(canonical(p['prefix']))
                if i==5:s.add(trajectory(tag=2,death_hp=20),'b')
            return out
        self.assertEqual(run(),run())
    def test_pool_is_bounded_and_keeps_the_best(self):
        s=FocusScheduler(elites=2,pool=3)
        for i,hp in enumerate((90,80,70,60,50)):s.add(trajectory(tag=i,death_hp=hp),f't{i}')
        snapshot=s.snapshot()
        self.assertEqual(snapshot['elite_pool'],3)
        self.assertEqual([e['label']for e in snapshot['elites']],['t4','t3'])
        self.assertEqual(set(s.sources),{'t4','t3','t2'})
    def test_non_terminal_and_invalid_results_are_not_focus_sources(self):
        s=FocusScheduler();r=trajectory();r['status']='BUDGET';r['value']=None;s.add(r,'alive')
        s.add({'status':'UNKNOWN','reason':'NATIVE_TASK_TIMEOUT','trace':[],'decision_evidence':[]},'timeout')
        self.assertEqual(s.snapshot()['elite_pool'],0)
        self.assertEqual(s.next()['category']!='focus',True)
    def test_scope_prefix_is_never_escaped(self):
        r=trajectory();scope=r['trace'][:20]
        s=FocusScheduler(scope);s.add(r,'root')
        for _ in range(40):
            p=s.next();self.assertGreaterEqual(p['index'],20);self.assertEqual(p['prefix'][:20],scope)
    def test_wide_menus_are_deferred_in_the_explorer_but_reachable_through_focus(self):
        r=trajectory(floors=2,options=40);s=FocusScheduler(site_cap=4);s.add(r,'root')
        self.assertEqual(s.explorer.snapshot()['options_deferred_by_site_cap'],4*(39-4))
        seen=set()
        while (p:=s.next())is not None:seen.add(canonical(p['prefix']))
        self.assertEqual(len(seen),4*39)
    def test_site_cap_none_keeps_the_historical_indexed_order(self):
        r=trajectory(labels=False);a=IndexedStrategicScheduler();b=IndexedStrategicScheduler(None,None)
        a.add(copy.deepcopy(r),'r');b.add(copy.deepcopy(r),'r')
        self.assertEqual([a.next()for _ in range(30)],[b.next()for _ in range(30)])
    def test_invalid_configuration_is_rejected(self):
        with self.assertRaises(ValueError):FocusScheduler(elites=4,pool=2)
        with self.assertRaises(ValueError):FocusScheduler(focus=0)
        with self.assertRaises(ValueError):IndexedStrategicScheduler(None,0)
        with self.assertRaises(ValueError):FocusScheduler(cluster_cap=-1)
        with self.assertRaises(ValueError):FocusScheduler(cluster_percent=101)

class DeckFamilyTests(unittest.TestCase):
    """The cap moves focus evaluations between trajectories; it never removes a branch."""
    def fill(self,scheduler):
        # Five trajectories of one family reach further than the single one of the other family.
        for i,hp in enumerate((10,20,30,40,50)):scheduler.add(trajectory(tag=i,death_hp=hp,deck=blocky(['X%d'%i])),f'blocky{i}')
        scheduler.add(trajectory(tag=9,death_hp=60,deck=strength()),'strength')
    def test_without_a_cap_one_family_takes_every_elite_place(self):
        s=FocusScheduler(elites=3,explore=0);self.fill(s)
        self.assertEqual({s.next()['source']for _ in range(30)},{'blocky0','blocky1','blocky2'})
        self.assertNotIn('deck_cluster_cap',s.snapshot())
    def test_cap_gives_the_other_family_an_elite_place(self):
        s=FocusScheduler(elites=3,explore=0,cluster_cap=2);self.fill(s)
        sources=[s.next()['source']for _ in range(40)]
        self.assertEqual(set(sources),{'blocky0','blocky1','strength'})
        self.assertGreater(sources.count('blocky0'),sources.count('strength'))     # rank still decides the share
        snapshot=s.snapshot()
        self.assertEqual([e['label']for e in snapshot['elites']],['blocky0','blocky1','strength'])
        self.assertEqual([e['deck_family']for e in snapshot['elites']],[0,0,2]);self.assertEqual(snapshot['elite_deck_families'],2)
    def test_crowded_out_trajectories_stay_reachable_through_the_explorer(self):
        s=FocusScheduler(elites=3,cluster_cap=1);self.fill(s);seen=set()
        while(p:=s.next())is not None:seen.add(canonical(p['prefix']))
        reference=FocusScheduler(elites=3);self.fill(reference);expected=set()
        while(p:=reference.next())is not None:expected.add(canonical(p['prefix']))
        self.assertEqual(seen,expected)
    def test_full_pool_drops_the_worst_member_of_a_crowded_family_first(self):
        s=FocusScheduler(elites=2,pool=3,cluster_cap=1)
        s.add(trajectory(tag=0,death_hp=10,deck=blocky()),'b-best');s.add(trajectory(tag=1,death_hp=20,deck=blocky(['A'])),'b-second')
        s.add(trajectory(tag=2,death_hp=70,deck=strength()),'s-far-behind')
        # A weaker trajectory of a new family replaces the crowded second blocky deck, not the weakest overall.
        s.add(trajectory(tag=3,death_hp=90,deck=['CLASH']*10),'other-family')
        self.assertEqual(set(s.sources),{'b-best','s-far-behind','other-family'})
        # A newcomer of a full family is left out even though it beats pool members of other families.
        s.add(trajectory(tag=4,death_hp=30,deck=blocky(['B'])),'b-third')
        self.assertEqual(set(s.sources),{'b-best','s-far-behind','other-family'})
        # A better trajectory of the full family replaces that family's member.
        s.add(trajectory(tag=5,death_hp=5,deck=blocky(['C'])),'b-new-best')
        self.assertEqual(set(s.sources),{'b-new-best','s-far-behind','other-family'})
        self.assertEqual(s.snapshot()['crowded_out_trajectories'],3)
    def test_an_exhausted_elite_frees_its_family_place(self):
        s=FocusScheduler(elites=1,explore=0,cluster_cap=1)
        s.add(trajectory(floors=2,tag=0,death_hp=10,deck=blocky()),'short')
        s.add(trajectory(floors=2,tag=1,death_hp=20,deck=blocky(['A'])),'sibling')
        sources=[]
        while(p:=s.next())is not None:sources.append(p['source'])
        self.assertEqual(sources[0],'short');self.assertIn('sibling',sources)
        self.assertLess(max(i for i,x in enumerate(sources)if x=='short'),min(i for i,x in enumerate(sources)if x=='sibling'))
    def test_missing_decks_count_as_one_family(self):
        s=FocusScheduler(elites=3,explore=0,cluster_cap=1)
        for i,hp in enumerate((10,20,30)):s.add(trajectory(tag=i,death_hp=hp),f't{i}')
        self.assertEqual({s.next()['source']for _ in range(12)},{'t0'})
    def test_cap_keeps_the_order_reproducible(self):
        def run():
            s=FocusScheduler(elites=3,cluster_cap=2);self.fill(s)
            return [canonical(s.next()['prefix'])for _ in range(40)]
        self.assertEqual(run(),run())

class Records(Values):
    """Stand-in gate model: a fixed family record per marker card, a fixed novelty per label."""
    def __init__(self,records=None,values=None,novelty=None):
        super().__init__(values or{});self.records=records or{};self.fresh=novelty or{};self.asked=[]
    def best(self,names,act,optimism=0.0):return max((self.values.get(n,0.0)+optimism*self.fresh.get(n,0.0)for n in names),default=0.0)
    def family(self,gate,deck,percent,memo):
        self.asked.append(gate);return next((record for card,record in self.records.items()if deck[card]),(0,0))
    optimistic=staticmethod(GateModels.optimistic)

class FamilyRecordTests(unittest.TestCase):
    """The record moves focus evaluations between elites; it never removes a branch or an elite."""
    fill=DeckFamilyTests.fill
    def test_a_family_that_keeps_failing_gives_way_to_one_with_less_evidence(self):
        model=Records({'IRON_WAVE':(40,0),'INFLAME':(4,2)})
        s=FocusScheduler(elites=3,explore=0,cluster_cap=2,models=model,family=True);self.fill(s)
        sources=[s.next()['source']for _ in range(60)]
        self.assertGreater(sources.count('strength'),sources.count('blocky0'))
        self.assertGreater(sources.count('blocky0'),sources.count('blocky1'));self.assertGreater(sources.count('blocky1'),0)
        self.assertEqual(set(model.asked),{(1,0)})                                    # the boss every trajectory died at
        rows=s.snapshot()['focus_family']
        self.assertEqual([(r['label'],r['family_entries'],r['family_passed'])for r in rows],[('blocky0',40,0),('blocky1',40,0),('strength',4,2)])
        self.assertEqual(sum(r['focus_evaluations']for r in rows),60)
    def test_without_the_switch_the_model_is_never_asked_and_the_order_is_unchanged(self):
        model=Records({'IRON_WAVE':(40,0),'INFLAME':(4,2)})
        a=FocusScheduler(elites=3,cluster_cap=2,models=model);b=FocusScheduler(elites=3,cluster_cap=2);self.fill(a);self.fill(b)
        self.assertEqual([canonical(a.next()['prefix'])for _ in range(60)],[canonical(b.next()['prefix'])for _ in range(60)])
        self.assertEqual(model.asked,[]);self.assertNotIn('focus_family',a.snapshot());self.assertNotIn('focus_optimism_percent',a.snapshot())
    def test_every_branch_stays_reachable(self):
        s=FocusScheduler(elites=3,cluster_cap=2,models=Records({'IRON_WAVE':(400,0)}),family=True,optimism=100);self.fill(s);seen=set()
        while(p:=s.next())is not None:seen.add(canonical(p['prefix']))
        reference=FocusScheduler(elites=3);self.fill(reference);expected=set()
        while(p:=reference.next())is not None:expected.add(canonical(p['prefix']))
        self.assertEqual(seen,expected)
    def test_record_works_without_the_cap_and_with_real_gate_models(self):
        models=GateModels(7);s=FocusScheduler(elites=3,explore=0,models=models,family=True)
        for i,hp in enumerate((10,20,30)):
            result=trajectory(tag=i,death_hp=hp,deck=blocky(['X%d'%i]));result['decision_evidence'][-1]['observation']['deck']=result['observation']['deck'];models.add(result,f't{i}');s.add(result,f't{i}')
        self.assertEqual({s.next()['source']for _ in range(12)},{'t0','t1','t2'})
        self.assertEqual([r['family_entries']for r in s.snapshot()['focus_family']],[3,3,3])
    def test_a_trajectory_that_died_after_a_boss_is_weighed_at_the_next_gate(self):
        model=Records();s=FocusScheduler(explore=0,models=model,family=True)
        result=trajectory(floors=20);result['observation']['room']='Monster';result['decision_evidence'][-1]['observation']['room']='Monster'
        s.add(result,'hallway');s.next()
        self.assertEqual(model.asked,[(1,0)])
    def test_optimism_tries_a_little_seen_option_first(self):
        def first(optimism):
            s=FocusScheduler(explore=0,models=Records(novelty={'card:33:2':1.0}),optimism=optimism);s.add(trajectory(),'root')
            return s.next()['prefix'][-1]['index']
        self.assertEqual((first(0),first(100)),(1,2))
    def test_invalid_family_configuration_is_rejected(self):
        with self.assertRaises(ValueError):FocusScheduler(family=True)
        with self.assertRaises(ValueError):FocusScheduler(optimism=100)
        with self.assertRaises(ValueError):FocusScheduler(models=Records(),optimism=-1)
        with self.assertRaises(ValueError):FocusScheduler(models=Records(),family=1)
    def test_record_keeps_the_order_reproducible(self):
        def run():
            s=FocusScheduler(elites=3,cluster_cap=2,models=Records({'IRON_WAVE':(40,0),'INFLAME':(4,2)}),family=True,optimism=100);self.fill(s)
            return [canonical(s.next()['prefix'])for _ in range(40)]
        self.assertEqual(run(),run())

def later_boss(tag=0,hp_in=40,death_hp=50,deck=None):
    """Passes a boss on floor 33 and dies in a second one on floor 34, entered with `hp_in` of 80 HP (no rest in between)."""
    result=trajectory(floors=33,tag=tag,death_hp=death_hp,deck=deck)
    first=result['decision_evidence'][-1];second=copy.deepcopy(first)
    first['observation'].update(hp='80',max_hp='80');second['observation'].update(floor=34,hp=str(hp_in),max_hp='80')
    move={'kind':'map','index':0}
    result['trace']+=[move,{'kind':'end_turn'}]
    result['decision_evidence']+=[{'phase':'map','observation':{'floor':33,'act':1,'hp':str(hp_in),'max_hp':'80'},'available_actions':[move]},second]
    result['terminal_combat']['floor']=34;result['observation']['floor']=34
    return result

class CarriedHpTests(unittest.TestCase):
    """Elite places alternate between the pool order and the HP carried into a lost later boss; nothing else moves."""
    fill=DeckFamilyTests.fill
    def places(self,s):return [row['label']for row in s.snapshot()['focus_carry']]
    def four(self,s):
        # Progress of the lost fight and HP carried into it point in opposite directions.
        for tag,(label,death_hp,hp_in)in enumerate((('far-low',10,8),('far-mid',20,24),('near-high',80,56),('near-top',90,60))):
            s.add(later_boss(tag=tag,death_hp=death_hp,hp_in=hp_in),label)
    def test_places_alternate_between_fight_progress_and_carried_hp(self):
        s=FocusScheduler(elites=4,explore=0,carry=True);self.four(s)
        self.assertEqual(self.places(s),['far-low','near-top','far-mid','near-high'])
        self.assertEqual([row['carried_hp']for row in s.snapshot()['focus_carry']],[.1,.75,.3,.7])
        first=[s.next()['source']for _ in range(25)]
        self.assertGreater(first.count('far-low'),first.count('near-top'));self.assertGreater(first.count('near-top'),first.count('far-mid'))
        plain=FocusScheduler(elites=4,explore=0);self.four(plain)
        self.assertEqual([row['label']for row in plain.snapshot()['elites']],['far-low','far-mid','near-high','near-top'])
        self.assertNotIn('focus_carry',plain.snapshot())
    def test_fewer_places_than_trajectories_keep_the_best_of_each_order(self):
        s=FocusScheduler(elites=2,explore=0,carry=True);self.four(s)
        self.assertEqual({s.next()['source']for _ in range(12)},{'far-low','near-top'})
    def test_carried_hp_never_reorders_trajectories_that_died_in_different_fights(self):
        s=FocusScheduler(elites=4,explore=0,carry=True);self.four(s)
        s.add(trajectory(floors=40,tag=7,death_hp=95),'deeper');s.add(trajectory(floors=33,tag=8,death_hp=1),'first-boss')
        self.assertEqual(self.places(s),['deeper','far-low','near-top','far-mid'])
        rows={row['label']:row['carried_hp']for row in s.snapshot()['focus_carry']};self.assertEqual(rows['deeper'],0)
        self.assertEqual(s.sources['first-boss'].carried,0)                              # a first boss carries nothing over
        self.assertEqual([label for _,_,label in s.pool],['deeper','far-low','far-mid','near-high','near-top','first-boss'])
    def test_without_a_later_boss_the_schedule_is_the_one_without_the_switch(self):
        a=FocusScheduler(elites=3,cluster_cap=2,carry=True);b=FocusScheduler(elites=3,cluster_cap=2);self.fill(a);self.fill(b)
        self.assertEqual([canonical(a.next()['prefix'])for _ in range(60)],[canonical(b.next()['prefix'])for _ in range(60)])
    def test_the_deck_cap_still_applies_to_the_alternating_places(self):
        s=FocusScheduler(elites=3,explore=0,cluster_cap=1,carry=True)
        s.add(later_boss(tag=0,death_hp=10,hp_in=8,deck=blocky()),'b-far');s.add(later_boss(tag=1,death_hp=20,hp_in=32,deck=strength()),'s-mid')
        s.add(later_boss(tag=2,death_hp=30,hp_in=56,deck=blocky(['A'])),'b-high')
        self.assertEqual(self.places(s),['b-far','s-mid'])
    def test_every_branch_stays_reachable_and_the_order_is_reproducible(self):
        def proposals(**options):
            s=FocusScheduler(elites=2,**options);self.four(s);out=[]
            while(p:=s.next())is not None:out.append(canonical(p['prefix']))
            return out
        carried=proposals(carry=True);plain=proposals()
        self.assertEqual(set(carried),set(plain));self.assertEqual(len(carried),len(set(carried)))
        self.assertNotEqual(carried,plain);self.assertEqual(carried,proposals(carry=True))
    def test_invalid_switch_is_rejected(self):
        with self.assertRaises(ValueError):FocusScheduler(carry=1)

def passed_then_died(tag=0):
    """Passes the boss on floor 33 and dies in an ordinary fight on floor 34."""
    result=later_boss(tag=tag);result['decision_evidence'][-1]['observation']['room']='Monster';result['observation']['room']='Monster'
    return result

class StallTests(unittest.TestCase):
    """The first boss of a later act with many entries and no pass: every other focus pick of the trajectories
    that died in its act goes to the act before. Nothing else moves, and nothing is removed."""
    def fill(self,s,count=3):
        # Deaths in the boss on floor 33 (second act, floors 18-33), each entered with a different prefix.
        for tag in range(count):s.add(trajectory(floors=33,tag=tag,death_hp=90-10*tag),f't{tag}')
    def stepped(self,s,count):return ['step_back'in s.next()for _ in range(count)]
    def test_below_the_threshold_the_schedule_is_the_one_without_the_switch(self):
        a=FocusScheduler(elites=3,stall=4);b=FocusScheduler(elites=3);self.fill(a);self.fill(b)
        self.assertEqual([canonical(a.next()['prefix'])for _ in range(60)],[canonical(b.next()['prefix'])for _ in range(60)])
        report=a.snapshot()['focus_stall'];json.dumps(a.snapshot())
        self.assertEqual((report['furthest_gate'],report['step_back_act'],report['step_back_evaluations']),([1,0],None,0))
        self.assertEqual(report['gates'],[{'gate':[1,0],'entries':3,'passes':0}]);self.assertNotIn('focus_stall',b.snapshot())
    def test_a_stalled_first_boss_sends_every_other_focus_pick_one_act_back(self):
        s=FocusScheduler(elites=3,explore=0,stall=3);self.fill(s)
        picks=[s.next()for _ in range(20)]
        self.assertEqual(['step_back'in p for p in picks],[False,True]*10)
        self.assertTrue(all((p['floor']<=17)==('step_back'in p)for p in picks))
        self.assertEqual(picks[0]['floor'],33);self.assertEqual(picks[1]['floor'],17)   # each starts at the end of its act
        report=s.snapshot()['focus_stall']
        self.assertEqual((report['step_back_act'],report['step_back_evaluations'],report['usual_evaluations_while_stalled']),(0,10,10))
    def test_a_retry_of_the_same_entry_is_not_a_new_entry(self):
        s=FocusScheduler(elites=3,explore=0,stall=3)
        for label in('first','again','third'):s.add(trajectory(floors=33,tag=0,death_hp=90),label)
        s.add(trajectory(floors=33,tag=1,death_hp=80),'other')
        self.assertEqual(s.snapshot()['focus_stall']['gates'],[{'gate':[1,0],'entries':2,'passes':0}])
        self.assertFalse(any(self.stepped(s,12)))
    def test_a_pass_ends_the_stall(self):
        s=FocusScheduler(elites=3,explore=0,stall=3);self.fill(s)
        self.assertEqual(self.stepped(s,4),[False,True,False,True])
        s.add(passed_then_died(tag=7),'passed')
        report=s.snapshot()['focus_stall']
        self.assertEqual(report['gates'],[{'gate':[1,0],'entries':4,'passes':1}]);self.assertIsNone(report['step_back_act'])
        self.assertFalse(any(self.stepped(s,20)))
    def test_a_second_boss_of_an_act_and_the_boss_of_the_first_act_never_trigger(self):
        s=FocusScheduler(elites=3,explore=0,stall=3)
        for tag in range(4):s.add(later_boss(tag=tag,death_hp=90-10*tag),f'l{tag}')
        report=s.snapshot()['focus_stall']
        self.assertEqual(report['gates'],[{'gate':[1,0],'entries':4,'passes':4},{'gate':[1,1],'entries':4,'passes':0}])
        self.assertIsNone(report['step_back_act']);self.assertFalse(any(self.stepped(s,20)))
        s=FocusScheduler(elites=3,explore=0,stall=3)
        for tag in range(4):s.add(trajectory(floors=17,tag=tag,death_hp=90-10*tag),f'a{tag}')
        self.assertEqual(s.snapshot()['focus_stall']['gates'],[{'gate':[0,0],'entries':4,'passes':0}]);self.assertFalse(any(self.stepped(s,20)))
    def test_a_trajectory_that_died_before_the_act_of_the_gate_keeps_its_usual_picks(self):
        s=FocusScheduler(elites=4,explore=0,stall=3);self.fill(s);s.add(trajectory(floors=10,tag=9),'early')
        picks=[s.next()for _ in range(40)];early=[p for p in picks if p['source']=='early']
        self.assertTrue(early);self.assertFalse(any('step_back'in p for p in early))
        report=s.snapshot()['focus_stall'];self.assertEqual(report['step_back_act'],0)
        self.assertEqual(report['step_back_evaluations']+report['usual_evaluations_while_stalled'],40-len(early))
        self.assertEqual(report['step_back_evaluations'],(40-len(early))//2)
    def test_without_a_pending_alternative_in_the_act_before_the_usual_pick_is_made(self):
        # The scope prefix covers the first act: no decision of that act can be revisited.
        result=trajectory(floors=33);scope=result['trace'][:34]
        s=FocusScheduler(scope,explore=0,stall=1);s.add(result,'only');p=FocusScheduler(scope,explore=0);p.add(result,'only')
        stalled=[];plain=[]
        while(g:=s.next())is not None:stalled.append(g)
        while(g:=p.next())is not None:plain.append(g)
        self.assertTrue(stalled);self.assertEqual(stalled,plain)
        self.assertEqual(s.snapshot()['focus_stall']['step_back_act'],0);self.assertEqual(s.snapshot()['focus_stall']['step_back_evaluations'],0)
    def test_every_branch_stays_reachable_and_the_order_is_reproducible(self):
        def proposals(**options):
            s=FocusScheduler(elites=2,**options);self.fill(s);out=[]
            while(p:=s.next())is not None:out.append(canonical(p['prefix']))
            return out
        stalled=proposals(stall=3);plain=proposals()
        self.assertEqual(set(stalled),set(plain));self.assertEqual(len(stalled),len(set(stalled)))
        self.assertNotEqual(stalled,plain);self.assertEqual(stalled,proposals(stall=3))
    def test_the_other_switches_keep_working_with_it(self):
        s=FocusScheduler(elites=3,explore=0,cluster_cap=1,carry=True,stall=3)
        for tag,deck in enumerate((blocky(),strength(),blocky(['A']),strength(['B']))):s.add(trajectory(floors=33,tag=tag,death_hp=90-10*tag,deck=deck),f'd{tag}')
        picks=[s.next()for _ in range(24)]
        self.assertEqual(['step_back'in p for p in picks],[False,True]*12);self.assertEqual(len({p['source']for p in picks}),2)
    def test_invalid_switch_is_rejected(self):
        with self.assertRaises(ValueError):FocusScheduler(stall=-1)
        with self.assertRaises(ValueError):FocusScheduler(stall=True)

if __name__=='__main__':unittest.main()
