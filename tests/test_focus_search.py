"""Main-loop wiring of the focus scheduler, gate retries and per-seed prior (no native process)."""
import copy,json,tempfile,unittest
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import patch
from spire_exact.canonical import canonical
from spire_exact.mode1 import context
from spire_exact.planning.search import SearchConfig,Evaluator,solve
from spire_exact.planning.gates import preset

CTX=context('42','IRONCLAD',10,'all')

def card(name):return {'id':name,'upgrade':0}

def world(history,prior=None,map_step=False):
    """Deterministic toy campaign: three card decisions, then a boss. GOOD cards win the boss,
    after which the route dies in a hallway fight; every other deck dies inside the boss.
    Past the replayed history the rollout takes the option with the highest tier (first one on ties).
    `map_step` adds the move into the boss room as a decision of its own (what a probe re-enters by)."""
    picks=[a.get('card')for a in history if a.get('kind')=='card_reward']
    trace=[];evidence=[];deck=[card('STRIKE')]
    def tier(option):return ((prior or {}).get('card:'+option['card'])or[0])[0]
    for floor in range(1,4):
        options=[{'kind':'card_reward','index':i,'card':c}for i,c in enumerate(('BAD','GOOD','MEH'))]
        chosen=next((o for o in options if len(picks)>=floor and o['card']==picks[floor-1]),None)
        if chosen is None:chosen=options[0] if len(picks)>=floor else max(options,key=tier)
        evidence.append({'phase':'card_reward','observation':{'act':0,'floor':floor,'hp':'60','max_hp':'80','deck':list(deck),'relics':[],'potions':[None]},
            'available_actions':options,'option_labels':[['card:'+o['card']]for o in options]})
        trace.append(chosen);deck.append(card(chosen['card']))
    if map_step:
        move={'kind':'map','col':0,'row':3}
        trace.append(move);evidence.append({'phase':'map','observation':{'act':0,'floor':3,'hp':'60','max_hp':'80','deck':list(deck),'relics':[],'potions':[None]},
            'available_actions':[move],'option_labels':[['go:Boss@a0']]})
    entry={'act':0,'floor':4,'room':'Boss','turn':1,'hp':'60','max_hp':'80','deck':list(deck),'relics':[],'potions':[None],
           'enemies':[{'id':'BOSS','combat_id':1,'hp':'100'}]}
    trace.append({'kind':'end_turn'});evidence.append({'phase':'combat','observation':entry,'available_actions':[{'kind':'end_turn'}]})
    good=sum(c['id']=='GOOD'for c in deck)
    if good<2:
        return {'status':'TERMINAL','value':[0],'reason':None,'trace':trace,'decision_evidence':evidence,'native_terminal_observed':False,
                'observation':{'act':0,'floor':4,'room':'Boss','hp':'0','max_hp':'80','gold':0,'potions':[None],'deck':deck,'relics':[]},
                'terminal_combat':{'act':0,'floor':4,'turn':3,'enemies':[{'id':'BOSS','combat_id':1,'hp':str(80-30*good),'max_hp':'100'}]}}
    after=dict(entry,room=None,turn=None,enemies=None,hp='35')
    trace.append({'kind':'reward','index':0});evidence.append({'phase':'rewards','observation':after,'available_actions':[{'kind':'reward','index':0}]})
    return {'status':'TERMINAL','value':[0],'reason':None,'trace':trace,'decision_evidence':evidence,'native_terminal_observed':False,
            'observation':{'act':0,'floor':6,'room':'Monster','hp':'0','max_hp':'80','gold':0,'potions':[None],'deck':deck,'relics':[]}}

def fake_probe(request):
    """What the toy boss does to the deck of a probe request: the same rule as `world`."""
    deck=['STRIKE']+[a['card']for a in request['history']if a.get('kind')=='card_reward']
    for edit in request['probe']['edits']:
        if edit['op']=='add':deck.append(edit['card'])
        elif edit['op']=='remove':deck.remove(edit['card'])
    good=deck.count('GOOD');common={'edits':request['probe']['edits'],'nodes':10,'search_seconds':.5}
    if good>=2:return {'won':True,'outcome':1+35/80,'lost':None,'hp':35.0,'max_hp':80.0,**common}
    return {'won':False,'outcome':None,'lost':(20.0+30*good,100.0),'hp':0.0,**common}

class FakeEvaluator(Evaluator):
    requests=[]
    # False keeps the historical toy world, whose rollouts ignore the tiers they are sent.
    policy_aware=False
    map_step=False
    def __init__(self,pool,ctx,directory,config,advisor):
        self.pool,self.ctx,self.directory,self.config,self.advisor=pool,ctx,directory,config,advisor
        self.cache=SimpleNamespace(hits=0);self.checkpoints=None;self.identity=None
        self.serial=0;self.records=[];self.started=perf_counter();self.deadline=self.started+100;self.inflight={};self.ready=[]
    def load_archive(self,paths):pass
    def dispatch(self,specs):
        for spec in specs:
            if self.serial>=self.config.evaluations:break
            request=spec['request']
            if spec['kind']=='gate_probe':
                label=f'eval-{self.serial:04d}-gate_probe';self.serial+=1
                for probe in spec['requests']:canonical(probe)
                FakeEvaluator.requests.append(('gate_probe',copy.deepcopy(spec['requests'])))
                outcomes=[fake_probe(probe)for probe in spec['requests']]
                self.records.append({'label':label,'kind':'gate_probe','classification':'SYNTHETIC_PROBE','probes':len(outcomes),
                                     'usable_probes':len(outcomes),'probe_search_seconds':.5*len(outcomes)})
                self.ready.append((spec,{'status':'PROBE_BATCH','synthetic':True,'outcomes':outcomes,'trace':[],'decision_evidence':[]},label))
                continue
            if request.get('generate_candidate'):
                tiers=self.tiers_for(spec)
                if tiers:request['policy_prior']=tiers
            canonical(request)                       # every request must be plain JSON
            label=f'eval-{self.serial:04d}-{spec["kind"]}';self.serial+=1
            FakeEvaluator.requests.append((spec['kind'],copy.deepcopy(request)))
            self.records.append({'label':label,'kind':spec['kind'],**({'family':spec['family']}if spec.get('family')else{})})
            self.ready.append((spec,world(request['history'],request.get('policy_prior')if FakeEvaluator.policy_aware else None,FakeEvaluator.map_step),label))
        return len(specs)
    def collect(self,timeout=.2,ordered=False):
        if not self.ready:return []
        if ordered:return [self.ready.pop(0)]
        rows=self.ready;self.ready=[];return rows
    def batch(self,specs):
        self.dispatch(specs);rows=self.ready;self.ready=[];return rows

def run(evaluations=40,policy_aware=False,map_step=False,**overrides):
    FakeEvaluator.requests=[];FakeEvaluator.policy_aware=policy_aware;FakeEvaluator.map_step=map_step
    options=dict(evaluations=evaluations,scheduler='focus',dispatch_mode='ordered',dispatch_window=4,repair_mode='gate',prior=True,
                 lookahead_floors=99,lookahead_actions=12000,max_decisions=12000,gate_retry_plans=tuple(preset('escalate')['retries']))
    options.update(overrides)
    advisor={'budget_ms':1000,'boss_budget_ms':1000,'nodes':60000,'gate_plans':preset('escalate')['plans']}
    with tempfile.TemporaryDirectory()as d,patch('spire_exact.planning.search.Evaluator',FakeEvaluator):
        pool=SimpleNamespace(stats={},resources=SimpleNamespace(as_dict=lambda:{},workers=2))
        result=solve(CTX,Path(d),pool,SearchConfig(**options),advisor=advisor)
        json.dumps(result)                                   # the report stays serialisable
        return result,list(FakeEvaluator.requests)

class FocusSearchTests(unittest.TestCase):
    def test_runs_to_budget_without_claiming_anything(self):
        result,requests=run()
        self.assertEqual(result['status'],'UNKNOWN');self.assertEqual((result['lower_bound'],result['upper_bound']),(0,1))
        self.assertEqual(len(requests),40);self.assertEqual(requests[0][0],'baseline')
        kinds={k for k,_ in requests}
        self.assertIn('macro_focus',kinds);self.assertIn('gate_retry',kinds)
        self.assertEqual(result['search_metrics']['scheduler'],'focus')
        self.assertIn('gate_models',result);self.assertIn('never a bound',result['gate_models']['scope'])
    def test_gate_retries_escalate_one_level_at_a_time_and_stay_within_their_share(self):
        _,requests=run()
        retries=[r for k,r in requests if k=='gate_retry']
        self.assertLessEqual(len(retries),.2*len(requests)+1)
        levels=preset('escalate')['retries']
        seen={}
        for r in retries:
            key=canonical(r['history']);level=seen.get(key,0)
            self.assertEqual(r['advisor']['gate_plans'],levels[level]['gate_plans']);seen[key]=level+1
            self.assertEqual(r['stop_at_floor'],4+99)
        self.assertTrue(all(v<=len(levels)for v in seen.values()))
    def test_only_close_fights_are_retried(self):
        def goods(r):return sum(a.get('card')=='GOOD'for a in r['history'])
        # One GOOD card removes 50 % of the boss, none removes 20 %.
        retries=[r for k,r in run()[1]if k=='gate_retry']
        self.assertTrue(retries);self.assertTrue(all(goods(r)==1 for r in retries))
        every=[r for k,r in run(gate_retry_percent=0)[1]if k=='gate_retry']
        self.assertTrue(any(goods(r)==0 for r in every))
        self.assertFalse([r for k,r in run(gate_retry_percent=51)[1]if k=='gate_retry'])
    def test_a_removed_enemy_life_counts_as_close(self):
        from spire_exact.planning.search import near_miss
        def fight(*hps):
            rows=[{'phase':'combat','observation':{'act':0,'floor':4,'turn':i+1,'enemies':[{'id':'BOSS','combat_id':1,'hp':str(h)}]}}for i,h in enumerate(hps)]
            return {'status':'TERMINAL','value':[0],'reason':None,'trace':[{}]*len(rows),'decision_evidence':rows,'observation':{'act':0,'floor':4}}
        self.assertFalse(near_miss(fight(100,90,80),50));self.assertTrue(near_miss(fight(100,60,45),50))
        self.assertTrue(near_miss(fight(100,40,0,200,190),50))      # second form barely scratched
        self.assertFalse(near_miss({'status':'TERMINAL','value':[0],'trace':[],'decision_evidence':[],'observation':{'act':0,'floor':4}},0))
    def test_ordinary_requests_keep_the_configured_gate_plan(self):
        _,requests=run()
        for kind,r in requests:
            if kind!='gate_retry':self.assertEqual(r['advisor']['gate_plans'],preset('escalate')['plans'])
    def test_prior_reaches_requests_once_the_gate_model_is_fitted_and_prefers_the_winning_card(self):
        _,requests=run(evaluations=80)
        with_prior=[r['policy_prior']for _,r in requests if r.get('policy_prior')]
        self.assertTrue(with_prior);self.assertNotIn('policy_prior',requests[0][1])
        self.assertTrue(any(p.get('card:GOOD',[0])[0]>0 for p in with_prior))
        self.assertTrue(all(type(t)is int for p in with_prior for row in p.values()for t in row))
    def test_prior_can_be_switched_off(self):
        _,requests=run(prior=False)
        self.assertTrue(all('policy_prior'not in r for _,r in requests))
    def test_search_finds_the_better_deck_first_with_focus(self):
        result,requests=run(evaluations=24,prior=False,repair_mode='fifo',gate_retry_plans=())
        self.assertEqual(result['best_observation']['floor'],6)
    def test_same_configuration_is_reproducible(self):
        a=[canonical(r)for _,r in run()[1]];b=[canonical(r)for _,r in run()[1]]
        self.assertEqual(a,b)
    def test_different_solver_seed_changes_only_the_sampled_tiers(self):
        a=run(evaluations=80,solver_seed=1)[1];b=run(evaluations=80,solver_seed=2)[1]
        self.assertNotEqual([r.get('policy_prior')for _,r in a],[r.get('policy_prior')for _,r in b])
    def test_invalid_configuration_is_rejected(self):
        with self.assertRaises(ValueError):SearchConfig(scheduler='focus',focus_elites=9,focus_pool=8)
        with self.assertRaises(ValueError):SearchConfig(gate_retry_plans=('not a patch',))
        with self.assertRaises(ValueError):SearchConfig(prior=1)
        with self.assertRaises(ValueError):SearchConfig(gate_retry_percent=101)
        with self.assertRaises(ValueError):SearchConfig(root_policies=('no-such-policy',))
        with self.assertRaises(ValueError):SearchConfig(root_policies=('native',))
        with self.assertRaises(ValueError):SearchConfig(root_policies=('pick','pick'))
        with self.assertRaises(ValueError):SearchConfig(focus_cluster_cap=-1)
        with self.assertRaises(ValueError):SearchConfig(focus_cluster_percent=0)
        with self.assertRaises(ValueError):SearchConfig(focus_family=1)
        with self.assertRaises(ValueError):SearchConfig(focus_optimism=-1)
        with self.assertRaises(ValueError):SearchConfig(focus_optimism=401)
        with self.assertRaises(ValueError):SearchConfig(focus_carry=1)
        with self.assertRaises(ValueError):SearchConfig(focus_stall=-1)
        with self.assertRaises(ValueError):SearchConfig(focus_stall=True)
    def test_family_record_and_optimism_are_off_by_default_and_never_change_a_request_format(self):
        config=SearchConfig();self.assertEqual((config.focus_family,config.focus_optimism),(False,0))
        plain,requests=run();self.assertNotIn('focus_family',plain['search_metrics'])
        result,steered=run(focus_family=True,focus_optimism=100,focus_cluster_cap=2)
        self.assertIn('focus_family',result['search_metrics']);self.assertEqual(result['search_metrics']['focus_optimism_percent'],100)
        self.assertEqual({k for k,_ in steered},{k for k,_ in requests})
        self.assertEqual({key for _,r in steered for key in r},{key for _,r in requests for key in r})
        self.assertEqual(run(focus_family=True,focus_optimism=100,focus_cluster_cap=2)[1],steered)
    def test_carried_hp_is_off_by_default_and_changes_nothing_without_a_later_boss(self):
        self.assertIs(SearchConfig().focus_carry,False)
        plain,requests=run();self.assertNotIn('focus_carry',plain['search_metrics'])
        # The toy campaign has one boss: no trajectory carries HP into a later one, so the schedule is the same.
        result,carried=run(focus_carry=True)
        self.assertEqual(carried,requests)
        self.assertTrue(all(row['carried_hp']==0 for row in result['search_metrics']['focus_carry']))
    def test_stalled_gate_switch_is_off_by_default_and_changes_nothing_without_an_act_to_step_back_to(self):
        self.assertEqual(SearchConfig().focus_stall,0)
        plain,requests=run();self.assertNotIn('focus_stall',plain['search_metrics'])
        # The toy campaign has one boss, in the first act: however many entries it has, there is no act before it.
        result,stalled=run(focus_stall=2)
        self.assertEqual(stalled,requests)
        report=result['search_metrics']['focus_stall']
        self.assertEqual((report['threshold_entries'],report['furthest_gate'],report['step_back_act'],report['step_back_evaluations']),(2,[0,0],None,0))
        self.assertGreaterEqual(report['gates'][0]['entries'],2)

TOY={'card:GOOD':[1]}

class RootPolicyTests(unittest.TestCase):
    def test_switches_are_off_by_default_and_add_nothing(self):
        config=SearchConfig()
        self.assertEqual((config.root_policies,config.focus_cluster_cap),((),0))
        result,requests=run(prior=False)
        self.assertNotIn('root_policies',result);self.assertEqual(requests[0][0],'baseline')
        self.assertTrue(all(k!='root_policy'and'policy_prior'not in r for k,r in requests))
        self.assertNotIn('evaluations_by_root_policy',result['search_metrics'])
    def test_shipped_tables_reach_the_root_rollouts_unchanged(self):
        from spire_exact.planning.policies import POLICIES
        _,requests=run(evaluations=8,prior=False,root_policies=('pick','elo'))
        self.assertEqual([k for k,_ in requests[:3]],['baseline','root_policy','root_policy'])
        self.assertNotIn('policy_prior',requests[0][1])
        self.assertEqual(requests[1][1]['policy_prior'],POLICIES['pick']);self.assertEqual(requests[2][1]['policy_prior'],POLICIES['elo'])
        self.assertTrue(all(r['history']==[]for _,r in requests[:3]))
    def test_a_lineage_keeps_the_policy_of_its_root(self):
        with patch.dict('spire_exact.planning.policies.POLICIES',{'toy':TOY}):
            result,requests=run(evaluations=60,policy_aware=True,prior=False,root_policies=('toy',))
        self.assertEqual(result['root_policies']['portfolio'],['native','toy'])
        # The toy policy takes GOOD three times, passes the boss and becomes the best trajectory.
        self.assertEqual(result['best_observation']['floor'],6)
        derived=[r for k,r in requests if r['history']]
        with_table=[r for r in derived if r.get('policy_prior')==TOY];without=[r for r in derived if'policy_prior'not in r]
        self.assertTrue(with_table);self.assertTrue(without);self.assertEqual(len(with_table)+len(without),len(derived))
        # A request deviating from the toy root shares that root's first picks.
        self.assertTrue(any(r['history'][0].get('card')=='GOOD'for r in with_table))
        counts=result['search_metrics']['evaluations_by_root_policy']
        self.assertEqual(sum(counts.values()),60)
        self.assertEqual(counts['toy'],sum(r.get('policy_prior')==TOY for _,r in requests))
        # Once nothing is left to deviate from, fresh restarts alternate between the policies.
        restarts=[r.get('policy_prior')for k,r in requests if k=='restart']
        self.assertEqual(restarts[:4],[None,TOY,None,TOY])
    def test_policy_tiers_add_to_the_learned_tiers(self):
        with patch.dict('spire_exact.planning.policies.POLICIES',{'toy':TOY}):
            _,requests=run(evaluations=120,policy_aware=True,root_policies=('toy',))
        tiers=[r['policy_prior'].get('card:GOOD',[0])[0]for _,r in requests if r.get('policy_prior')]
        self.assertIn(1,tiers)                               # the table alone, before any evidence
        self.assertTrue(any(t>=2 for t in tiers))            # table plus this seed's evidence for GOOD
        self.assertTrue(all(type(t)is int for _,r in requests if r.get('policy_prior')for row in r['policy_prior'].values()for t in row))
    def test_root_policies_are_reproducible(self):
        def once():
            with patch.dict('spire_exact.planning.policies.POLICIES',{'toy':TOY}):
                return [canonical(r)for _,r in run(evaluations=60,policy_aware=True,root_policies=('toy',))[1]]
        self.assertEqual(once(),once())
    def test_root_policies_need_a_fresh_single_root(self):
        from spire_exact.canonical import ContractError
        with patch.dict('spire_exact.planning.policies.POLICIES',{'toy':TOY}),tempfile.TemporaryDirectory()as d,\
                patch('spire_exact.planning.search.Evaluator',FakeEvaluator):
            pool=SimpleNamespace(stats={},resources=SimpleNamespace(as_dict=lambda:{},workers=2))
            config=SearchConfig(evaluations=4,scheduler='focus',root_policies=('toy',))
            with self.assertRaises(ContractError):
                solve(CTX,Path(d),pool,config,advisor=None,initial_prefix=[{'kind':'card_reward','index':1,'card':'GOOD'}])

class GateProbeTests(unittest.TestCase):
    """Synthetic probes feed the gate model's label values and nothing else."""
    def first_good_tier(self,requests):
        return next((i for i,(k,r)in enumerate(requests)if k!='gate_probe'and(r.get('policy_prior')or{}).get('card:GOOD',[0])[0]>0),None)
    def test_off_by_default(self):
        result,requests=run(map_step=True)
        self.assertEqual(SearchConfig().gate_probe_schedule,())
        self.assertNotIn('gate_probe',{k for k,_ in requests});self.assertNotIn('gate_probes',result['search_metrics'])
        self.assertNotIn('probe_tables',result['gate_models']['gates'][0])
    def test_table_is_probed_from_the_first_lost_entry(self):
        result,requests=run(evaluations=30,map_step=True,gate_probe_schedule=(1,),gate_probe_chunk=4)
        self.assertEqual([k for k,_ in requests[:3]],['baseline','gate_probe','gate_probe'])
        probes=[p for k,batch in requests if k=='gate_probe'for p in batch]
        self.assertEqual(len(probes),8);self.assertEqual(probes[0]['probe']['edits'],[])           # the unedited base comes first
        for p in probes:
            self.assertEqual([a['card']for a in p['history']],['BAD','BAD','BAD'])
            self.assertEqual(p['probe']['enter'],{'kind':'map','col':0,'row':3})
            self.assertFalse(p['capture_checkpoints'])
            for key in('checkpoint','policy_prior','stop_at_floor'):self.assertNotIn(key,p)
        self.assertEqual(sorted(e['op']+':'+e['card']for p in probes for e in p['probe']['edits']),
                         ['add:BAD','add:GOOD','add:MEH','remove:BAD','remove:STRIKE','upgrade:BAD','upgrade:STRIKE'])
        metrics=result['search_metrics']['gate_probes']
        self.assertEqual((metrics['batches'],metrics['probes'],metrics['usable'],metrics['tables_scheduled']),(2,8,8,{'[0, 0]':1}))
        gate=result['gate_models']['gates'][0]
        self.assertEqual((gate['probe_tables'],gate['probe_top'][0][0]),(1,'card:GOOD'));self.assertGreater(gate['probe_top'][0][1],4)
    def test_probe_values_reach_rollouts_before_the_gate_model_is_fitted(self):
        _,with_probes=run(evaluations=30,map_step=True,gate_probe_schedule=(1,),gate_probe_chunk=4)
        _,without=run(evaluations=30,map_step=True)
        early=self.first_good_tier(with_probes);late=self.first_good_tier(without)
        self.assertIsNotNone(early);self.assertLess(early,10)
        self.assertTrue(late is None or late>early+10)             # the regressions need 24 real entries first
    def test_probed_values_steer_a_policy_aware_rollout_to_the_passing_deck(self):
        result,_=run(evaluations=14,policy_aware=True,map_step=True,gate_probe_schedule=(1,),gate_probe_chunk=4)
        self.assertEqual(result['best_observation']['floor'],6)
    def test_probes_never_become_trajectories(self):
        result,requests=run(evaluations=30,map_step=True,gate_probe_schedule=(1,),gate_probe_chunk=4)
        real=sum(k!='gate_probe'for k,_ in requests)
        self.assertNotIn('gate_probe',result['best_label'])
        self.assertTrue(all('gate_probe'not in row['label']for row in result['frontier']))
        self.assertTrue(all('gate_probe'not in row['label']for row in result['failures']))
        self.assertLessEqual(result['search_metrics']['candidates_completed'],real)
        self.assertLessEqual(result['gate_models']['gates'][0]['entries'],real)
        self.assertEqual(result['search_metrics']['valid_evaluations'],real)
        self.assertEqual((result['status'],result['lower_bound'],result['upper_bound']),('UNKNOWN',0,1))
    def test_later_tables_follow_the_schedule_and_use_the_closest_lost_entry(self):
        result,requests=run(evaluations=40,map_step=True,gate_probe_schedule=(1,4),gate_probe_chunk=16)
        batches=[batch for k,batch in requests if k=='gate_probe']
        self.assertEqual(len(batches),2);self.assertEqual(result['search_metrics']['gate_probes']['tables_scheduled'],{'[0, 0]':2})
        # By the fourth distinct entry a deck with one GOOD card (50 % removed) exists: it is the second base.
        self.assertEqual(sum(a.get('card')=='GOOD'for a in batches[1][0]['history']),1)
        self.assertEqual(result['gate_models']['gates'][0]['probe_tables'],2)
    def test_probing_is_reproducible(self):
        def once():return [canonical(r)if k!='gate_probe'else canonical({'probes':r})for k,r in run(evaluations=30,map_step=True,gate_probe_schedule=(1,4))[1]]
        self.assertEqual(once(),once())
    def test_invalid_probe_configuration_is_rejected(self):
        for schedule in((0,),(8,8),(64,8),('8',),8):
            with self.assertRaises(ValueError):SearchConfig(gate_probe_schedule=schedule)
        with self.assertRaises(ValueError):SearchConfig(gate_probe_plan='light')
        with self.assertRaises(ValueError):SearchConfig(gate_probe_chunk=0)
        from spire_exact.canonical import ContractError
        with tempfile.TemporaryDirectory()as d,patch('spire_exact.planning.search.Evaluator',FakeEvaluator):
            pool=SimpleNamespace(stats={},resources=SimpleNamespace(as_dict=lambda:{},workers=2))
            with self.assertRaises(ContractError):                   # probes need the combat advisor
                solve(CTX,Path(d),pool,SearchConfig(evaluations=4,scheduler='focus',gate_probe_schedule=(1,)),advisor=None)

class PolicyTableTests(unittest.TestCase):
    def test_merge_adds_per_act_and_repeats_the_last_entry(self):
        from spire_exact.planning.policies import merge
        self.assertEqual(merge({},{'card:A':[1]}),{'card:A':[1]})
        self.assertEqual(merge({'card:A':[2,2,2]},{}),{'card:A':[2,2,2]})
        merged=merge({'card:A':[2,1,-1],'card:B':[-2,-2,-2]},{'card:A':[1],'card:B':[2,0],'card:C':[0,-1]})
        self.assertEqual(merged,{'card:A':[3,2,0],'card:B':[0,-2,-2],'card:C':[0,-1]})
        self.assertNotIn('card:Z',merge({'card:Z':[1]},{'card:Z':[-1]}))      # cancelled rows are dropped
    def test_merge_never_mutates_or_shares_the_table(self):
        from spire_exact.planning.policies import merge
        table={'card:A':[1,1,1]};out=merge(table,{})
        out['card:A'][0]=9;self.assertEqual(table,{'card:A':[1,1,1]})
    def test_shipped_tables_are_small_integers_and_match_their_generator_inputs(self):
        from spire_exact.planning.policies import POLICIES,NATIVE,resolve
        from spire_exact.planning.policy_data import SOURCE
        self.assertEqual(POLICIES[NATIVE],{});self.assertEqual(set(POLICIES),{'native','pick','elo'})
        for name in('pick','elo'):
            self.assertGreater(len(POLICIES[name]),100)
            for label,row in POLICIES[name].items():
                self.assertTrue(label.startswith('card:'));self.assertEqual(len(row),3)
                self.assertTrue(all(type(t)is int and-2<=t<=2 for t in row)and any(row))
        self.assertEqual(len(SOURCE['sha256']),3)
        self.assertEqual(resolve(['pick','native','elo']),('pick','elo'))
        with self.assertRaises(ValueError):resolve(['unknown'])

if __name__=='__main__':unittest.main()
