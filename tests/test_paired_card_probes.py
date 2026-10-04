"""Paired card probes: complete pairing, native scope, and synthetic isolation."""
import copy
import tempfile
import unittest
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import patch
from concurrent.futures import Future
from spire_exact.canonical import canonical
from spire_exact.mode1 import context
from spire_exact.planning.search import SearchConfig, Evaluator, solve
from spire_exact.planning.paired_card_probes import PairedCardProbes, candidates, rng_samples, SAMPLES, paired_outcome, probe_workload

CTX = context('42', 'IRONCLAD', 10, 'all')
TEMPLATE = {**{k:CTX[k] for k in ('seed','character','ascension','unlocks')},
    'history':[], 'generate_candidate':True, 'policy_seed':7,
    'advisor':{'budget_ms':600000, 'boss_budget_ms':600000, 'nodes':60000,
        'gate_plans':{'FinalBoss':{'members':[{'beam':270, 'nodes':540000}]} }},
    'capture_checkpoints':True, 'checkpoint':'unsafe', 'policy_prior':{'card:A':[1]}, 'stop_at_floor':99}


def trajectory(history=(), tiers=None, *, campaign=True, act=2, tag=0):
    trace, evidence = [], []
    deck = [{'id':'STRIKE','upgrade':0}]
    quiet = {'act':act, 'floor':40, 'hp':'60', 'max_hp':'80', 'deck':deck,
             'relics':[], 'potions':[None], 'gold':100, 'tag':tag,
             'room':None,'turn':None,'enemies':None,'hand':None,'energy':None,'block':'0',
             'strategic':{},'selection':None,'character':'IRONCLAD','rng':{'native_fixture_state':tag},'score':0}
    for floor in range(3):
        options = [{'kind':'card_reward','index':i,'card':card} for i,card in enumerate(('BAD','GOOD','MEH'))]+[{'kind':'card_skip'}]
        wanted = history[floor] if floor < len(history) else None
        chosen = next((o for o in options if wanted == o), None)
        if chosen is None:
            def tier(o):
                label = 'card:'+o['card'] if o.get('card') else 'card_skip'
                return ((tiers or {}).get('paired:'+label) or [0,0,0])[-1]
            chosen = max(options,key=tier)
        evidence.append({'phase':'card_reward','observation':copy.deepcopy(quiet), 'available_actions':options,
                         'option_labels':[['card:'+o['card']]if o.get('card')else['card_skip'] for o in options]})
        trace.append(chosen)
        if chosen.get('card'): deck.append({'id':chosen['card'], 'upgrade':0})
    move = {'kind':'map','col':tag,'row':15}
    evidence.append({'phase':'map','observation':copy.deepcopy(quiet),'available_actions':[move], 'option_labels':[['go:Boss@a2']]})
    trace.append(move)
    fight = copy.deepcopy(quiet);fight.update(floor=49,room='Boss',turn=1,enemies=[{'id':'F1','combat_id':1,'hp':'100'}])
    evidence.append({'phase':'combat','observation':fight,'available_actions':[{'kind':'end_turn'}]})
    trace.append({'kind':'end_turn'})
    result = {'status':'TERMINAL','value':[0],'reason':None,'native_terminal_observed':False,
        'trace':trace,'decision_evidence':evidence,
        'observation':dict(quiet,floor=49,room='Boss',hp='0'),
        'terminal_combat':{'act':act,'floor':49,'turn':2,'enemies':[{'id':'F1','combat_id':1,'hp':'40','max_hp':'100'}]}}
    if campaign:result['campaign']={'act_count':3,'final_act_boss_count':2}
    return result


def loss(removed, total=100):
    return {'won':False, 'outcome':None, 'lost':(float(removed),float(total)), 'nodes':10, 'search_seconds':0.5}


def fill(probes, specs, *, bad_sample=None):
    for spec in specs:
        outcomes = [loss(50), loss(20), loss(90), loss(55)]
        if spec['sample'] == bad_sample:outcomes[2] = None
        probes.absorb(spec,outcomes)


class PairedCardProbeTests(unittest.TestCase):
    def setUp(self):
        self.probes = PairedCardProbes(True,123,context=CTX,inputs={'game':'fingerprint'})
    def test_first_table_has_five_shared_samples_and_real_offered_candidates(self):
        result = trajectory()
        specs = self.probes.observe(result,'real',0,TEMPLATE)
        self.assertEqual(len(specs),SAMPLES)
        self.assertEqual([s['sample']for s in specs],list(rng_samples(123,0)))
        for spec in specs:
            self.assertEqual(spec['labels'],['card_skip','card:BAD','card:GOOD','card:MEH'])
            self.assertEqual(len(spec['requests']),4)
            for req in spec['requests']:
                self.assertEqual(req['history'],result['trace'][:3])
                self.assertEqual(req['probe']['rng'],spec['sample'])
                self.assertEqual(req['probe']['hp'],80)
                self.assertEqual(req['probe']['enter'],{'kind':'encounter','boss':1})
                self.assertFalse(req['capture_checkpoints'])
                for key in ('checkpoint','policy_prior','stop_at_floor'):self.assertNotIn(key,req)
                self.assertEqual(req['advisor']['gate_plans'],{'Boss':{'members':[{'mode':'Evaluate','beam':45,'nodes':120000}],'select':'auto'}})
                canonical(req)
            self.assertEqual(spec['requests'][0]['probe']['edits'],[])
            self.assertEqual(spec['requests'][1]['probe']['edits'],[{'op':'add','card':'BAD','upgrade':0}])
        self.assertEqual(TEMPLATE['advisor']['gate_plans']['FinalBoss']['members'][0]['beam'],270)
    def test_selection_fills_from_earlier_offers_and_breaks_frequency_ties_by_id(self):
        result = trajectory()
        result['decision_evidence'][0]['observation']['act']=0
        result['decision_evidence'][0]['option_labels']=[['card:Z'],['card:A'],['card:Y']]
        result['decision_evidence'][1]['option_labels']=[['card:C'],['card:C'],['card:B']]
        result['decision_evidence'][2]['option_labels']=[['card:B']]
        self.assertEqual(candidates(result,3,2),['B','C','A','Y','Z'])
        self.assertEqual(candidates(result,1,2),['A','Y','Z'])
    def test_missing_campaign_or_non_double_boss_is_inactive(self):
        self.assertEqual(self.probes.observe(trajectory(campaign=False),'old',0,TEMPLATE),[])
        self.assertEqual(self.probes.snapshot()['state'],'WAITING_FOR_NATIVE_METADATA')
        single=trajectory();single['campaign']['final_act_boss_count']=1
        self.assertEqual(self.probes.observe(single,'single',0,TEMPLATE),[])
        self.assertEqual(self.probes.snapshot()['state'],'INACTIVE')
        self.assertEqual(self.probes.snapshot()['distinct_entries'],0)
    def test_earlier_acts_and_replayed_fights_are_not_entries(self):
        self.assertEqual(self.probes.observe(trajectory(act=1),'early',0,TEMPLATE),[])
        self.assertEqual(self.probes.observe(trajectory(),'replayed',5,TEMPLATE),[])
        self.assertEqual(self.probes.snapshot()['distinct_entries'],0)
    def test_other_characters_ascensions_and_unlock_contexts_are_inactive(self):
        for changed in ({'character':'SILENT'},{'ascension':0},{'ascension':11},{'unlocks':'none'},{'ascension':True}):
            probes=PairedCardProbes(True,1)
            template={**TEMPLATE,**changed}
            self.assertEqual(probes.observe(trajectory(),'other',0,template),[])
            self.assertEqual(probes.snapshot()['inactive_reason'],'initial_context_outside_ironclad_a10_all_scope')
    def test_entry_identity_uses_full_prefix_and_context_bytes(self):
        result=trajectory()
        self.assertEqual(len(self.probes.observe(result,'one',0,TEMPLATE)),5)
        self.assertEqual(self.probes.observe(copy.deepcopy(result),'same',0,TEMPLATE),[])
        different=trajectory();different['trace'][0]['extra_state']=1
        self.probes.observe(different,'changed',0,TEMPLATE)
        other=copy.deepcopy(TEMPLATE);other['seed']='43'
        self.probes.observe(result,'context',0,other)
        self.probes.observe(trajectory(tag=1),'different_enter',0,TEMPLATE)
        self.assertEqual(self.probes.snapshot()['distinct_entries'],4)
    def test_thresholds_follow_distinct_entries_and_are_independent_of_clock(self):
        scheduled=[]
        for count in range(1,129):
            result=trajectory();result['trace'][0]['entry_serial']=count
            if self.probes.observe(result,str(count),0,TEMPLATE):scheduled.append(count)
        self.assertEqual(scheduled,[1,32,128])
        self.assertEqual(self.probes.snapshot()['next_entry_threshold'],512)
    def test_complete_table_is_required_and_signal_is_third_act_only(self):
        specs=self.probes.observe(trajectory(),'source',0,TEMPLATE)
        fill(self.probes,specs[:4]);self.assertEqual(self.probes.tiers(),{})
        fill(self.probes,specs[4:]);tiers=self.probes.tiers()
        self.assertEqual(tiers['paired:card_skip'],[0,0,0])
        self.assertEqual(tiers['paired:card:BAD'],[0,0,-1])
        self.assertEqual(tiers['paired:card:MEH'],[0,0,1])
        self.assertEqual(tiers['paired:card:GOOD'],[0,0,2])
        self.assertEqual(self.probes.snapshot()['tables'][0]['status'],'PAIRED_SIGNAL')
        self.assertTrue(all(label.startswith('paired:card:')or label=='paired:card_skip'for label in tiers))
        self.assertTrue(all(type(v)is int for row in tiers.values()for v in row))
    def test_missing_arm_keeps_table_unknown_and_never_creates_a_signal(self):
        specs=self.probes.observe(trajectory(),'source',0,TEMPLATE)
        fill(self.probes,specs,bad_sample=specs[2]['sample'])
        report=self.probes.snapshot()
        self.assertEqual(report['tables_with_signal'],0)
        self.assertEqual(report['tables'][0]['complete_paired_samples'],4)
        self.assertEqual(report['errors_or_missing'],1)
        self.assertEqual(self.probes.tiers(),{})
    def test_common_life_total_covers_all_arms_and_samples(self):
        specs=self.probes.observe(trajectory(),'source',0,TEMPLATE)
        for i,spec in enumerate(specs):
            self.probes.absorb(spec,[loss(50),loss(100,200 if i==4 else 100),loss(90),loss(55)])
        table=self.probes.snapshot()['tables'][0]
        self.assertEqual(table['life_total'],200)
        self.assertAlmostEqual(table['means']['card_skip'],0.25)
        self.assertAlmostEqual(table['paired_gains']['card:BAD'],0.25)
    def test_invalid_non_finite_or_wrong_arm_count_remains_unknown(self):
        for wrong in ([loss(50)], [loss(50),loss(20),{'won':True,'outcome':float('nan')},loss(55)],
                      [loss(50),loss(20),loss(0,0),loss(55)], [loss(50),loss(20),loss(150,100),loss(55)]):
            probes=PairedCardProbes(True,123)
            specs=probes.observe(trajectory(),'source',0,TEMPLATE)
            for spec in specs:probes.absorb(spec,wrong)
            self.assertEqual(probes.tiers(),{})
            self.assertEqual(probes.snapshot()['tables_with_signal'],0)
    def test_full_hp_uses_map_boundary_not_battle_start_hook_growth(self):
        result=trajectory();result['decision_evidence'][-1]['observation']['max_hp']='90'
        specs=self.probes.observe(result,'grew_on_enter',0,TEMPLATE)
        self.assertEqual(specs[0]['requests'][0]['probe']['hp'],80)
    def test_expected_map_state_covers_rng_deck_resources_and_does_not_alias(self):
        result=trajectory();specs=self.probes.observe(result,'source',0,TEMPLATE)
        expected=result['decision_evidence'][3]['observation']
        actual=specs[0]['requests'][0]['probe']['expected_entry_observation']
        self.assertEqual(canonical(actual),canonical(expected))
        self.assertIn('rng',actual);self.assertIn('deck',actual);self.assertIn('gold',actual)
        actual['deck'][0]['id']='CHANGED';actual['rng']['native_fixture_state']=99
        self.assertEqual(expected['deck'][0]['id'],'STRIKE')
        self.assertEqual(expected['rng']['native_fixture_state'],0)
        other=specs[0]['requests'][1]['probe']['expected_entry_observation']
        self.assertEqual(other['deck'][0]['id'],'STRIKE')
        self.assertEqual(other['rng']['native_fixture_state'],0)
    def test_missing_complete_native_map_observation_never_schedules(self):
        for missing in ('rng','deck','gold'):
            probes=PairedCardProbes(True,1);result=trajectory()
            del result['decision_evidence'][3]['observation'][missing]
            self.assertEqual(probes.observe(result,'incomplete',0,TEMPLATE),[])
            self.assertEqual(probes.snapshot()['tables_scheduled'],0)
    def test_rng_is_reproducible_distinct_and_bound_to_solver_and_table(self):
        self.assertEqual(rng_samples(123,0),rng_samples(123,0))
        self.assertEqual(len(set(rng_samples(123,0))),5)
        self.assertNotEqual(rng_samples(123,0),rng_samples(124,0))
        self.assertNotEqual(rng_samples(123,0),rng_samples(123,1))


class FakeEvaluator(Evaluator):
    requests=[]
    def __init__(self,pool,ctx,directory,config,advisor,**scope):
        self.pool,self.ctx,self.directory,self.config,self.advisor=pool,ctx,directory,config,advisor
        self.scope_prefix=scope.get('scope_prefix');self.stop_floor=scope.get('stop_floor')
        self.cache=SimpleNamespace(hits=0);self.checkpoints=None;self.identity=None
        self.serial=0;self.records=[];self.started=perf_counter();self.deadline=self.started+100
        self.inflight={};self.ready=[]
    def load_archive(self,paths):pass
    def dispatch(self,specs):
        for spec in specs:
            if self.serial>=self.config.evaluations:break
            kind=spec['kind'];label=f'eval-{self.serial:04d}-{kind}';self.serial+=1
            if kind=='paired_card_probe':
                outcomes=[loss(50 if not r['probe']['edits']else{'BAD':20,'GOOD':90,'MEH':55}[r['probe']['edits'][0]['card']])for r in spec['requests']]
                FakeEvaluator.requests.append((kind,copy.deepcopy(spec['requests'])))
                self.records.append({'label':label,'kind':kind,'classification':'SYNTHETIC_PROBE','probes':len(outcomes),'usable_probes':len(outcomes)})
                result={'status':'PROBE_BATCH','synthetic':True,'outcomes':outcomes,'trace':[],'decision_evidence':[]}
            else:
                request=spec['request'];tiers=self.tiers_for(spec)
                if tiers:request['policy_prior']=tiers
                FakeEvaluator.requests.append((kind,copy.deepcopy(request)))
                self.records.append({'label':label,'kind':kind,'classification':'NATIVE_ROUTE_DEATH'})
                result=trajectory(request['history'],tiers)
            self.ready.append((spec,result,label))
        return len(specs)
    def collect(self,timeout=.2,ordered=False):
        if not self.ready:return []
        if ordered:return [self.ready.pop(0)]
        rows=self.ready;self.ready=[];return rows


def run(enabled=True, advisor=True):
    FakeEvaluator.requests=[]
    with tempfile.TemporaryDirectory()as folder,patch('spire_exact.planning.search.Evaluator',FakeEvaluator):
        pool=SimpleNamespace(stats={},resources=SimpleNamespace(as_dict=lambda:{},workers=2),inputs={'binary':'fixed'})
        cfg=SearchConfig(evaluations=20,scheduler='focus',dispatch_mode='ordered',dispatch_window=4,
            max_decisions=12000,lookahead_actions=12000,paired_card_probes=enabled)
        result=solve(CTX,Path(folder),pool,cfg,advisor=TEMPLATE['advisor']if advisor else None)
        return result,copy.deepcopy(FakeEvaluator.requests)


class MainLoopPairedTests(unittest.TestCase):
    def test_default_is_enabled_with_explicit_disable_and_validation(self):
        self.assertTrue(SearchConfig().paired_card_probes)
        self.assertFalse(SearchConfig(paired_card_probes=False).paired_card_probes)
        with self.assertRaises(ValueError):SearchConfig(paired_card_probes=1)
    def test_synthetic_tables_steer_only_rollout_tiers_and_never_real_rows(self):
        result,requests=run()
        self.assertEqual(sum(kind=='paired_card_probe'for kind,_ in requests),5)
        self.assertTrue(any((r.get('policy_prior')or{}).get('paired:card:GOOD')==[0,0,2]for kind,r in requests if kind!='paired_card_probe'))
        report=result['search_metrics']['paired_card_probes']
        self.assertEqual((report['tables_scheduled'],report['tables_with_signal'],report['completed_batches']),(1,1,5))
        self.assertEqual(report['attempted_probes'],20)
        self.assertNotIn('paired_card_probe',result['best_label'])
        self.assertTrue(all('paired_card_probe'not in row['label']for row in result['frontier']))
        self.assertTrue(all('paired_card_probe'not in row['label']for row in result['failures']))
        self.assertTrue(all('probe_tables'not in gate for gate in result['gate_models']['gates']))
        self.assertEqual((result['status'],result['lower_bound'],result['upper_bound']),('UNKNOWN',0,1))
        self.assertEqual(result['exclusion_certificates'],[])
    def test_off_never_requests_metadata_or_submits_probes(self):
        result,requests=run(False)
        self.assertNotIn('paired_card_probe',{kind for kind,_ in requests})
        self.assertTrue(all('include_campaign_metadata'not in r for _,r in requests))
        self.assertNotIn('paired_card_probes',result['search_metrics'])
        self.assertEqual(requests,run(False)[1])
    def test_no_advisor_is_explicitly_inactive_without_failing(self):
        result,requests=run(advisor=False)
        report=result['search_metrics']['paired_card_probes']
        self.assertEqual((report['state'],report['inactive_reason']),('INACTIVE','no_combat_advisor'))
        self.assertNotIn('paired_card_probe',{kind for kind,_ in requests})
    def test_same_solver_and_submission_order_reproduce_requests(self):
        a=run()[1];b=run()[1]
        self.assertEqual([canonical({'kind':k,'requests':r})for k,r in a],
                         [canonical({'kind':k,'requests':r})for k,r in b])
    def test_scoped_diagnostic_does_not_request_metadata(self):
        with tempfile.TemporaryDirectory()as folder:
            pool=SimpleNamespace(stats={},resources=SimpleNamespace(as_dict=lambda:{},workers=2),inputs={})
            ev=Evaluator(pool,CTX,Path(folder),SearchConfig(),TEMPLATE['advisor'],scope_prefix=[],stop_floor=49)
            self.assertNotIn('include_campaign_metadata',ev.request([]))
            ev2=Evaluator(pool,CTX,Path(folder),SearchConfig(),None)
            self.assertNotIn('include_campaign_metadata',ev2.request([]))
            ev3=Evaluator(pool,CTX,Path(folder),SearchConfig(),TEMPLATE['advisor'])
            self.assertTrue(ev3.request([])['include_campaign_metadata'])
    def test_collector_rejects_non_synthetic_terminal_or_error_results(self):
        def raw():
            result=trajectory()
            result.update(status='PROBE',synthetic=True,value=None,
                consumed=3,probe={'edits':[],'entered':True,'fought':True,'won':False,'entry_index':3})
            return result
        probes=PairedCardProbes(True,1)
        spec=probes.observe(trajectory(),'real',0,TEMPLATE)[0]
        for changed in ({'synthetic':False},{'native_terminal_observed':True},{'value':[1]},{'reason':'timeout'},{'status':'UNSUPPORTED'}):
            result=raw();result.update(changed)
            future=Future();future.set_result([(result,None)]*len(spec['labels']))
            ev=object.__new__(Evaluator);ev.started=perf_counter();records=[];ev.record=records.append
            batch=ev.collect_probes(spec,future,'synthetic')
            self.assertEqual(batch['outcomes'],[None]*len(spec['labels']))
            self.assertEqual(records[0]['classification'],'SYNTHETIC_PROBE')
            self.assertIsNone(records[0]['observation'])
    def test_collector_marks_a_finished_probe_without_enemy_progress_unknown(self):
        probes=PairedCardProbes(True,1)
        specs=probes.observe(trajectory(),'real',0,TEMPLATE)
        ev=object.__new__(Evaluator);ev.started=perf_counter();records=[];ev.record=records.append
        for spec in specs:
            rows=[]
            for request in spec['requests']:
                result=trajectory()
                result.update(status='PROBE',synthetic=True,value=None,consumed=len(request['history']),
                    decision_evidence=[],terminal_combat=None,
                    probe={'edits':request['probe']['edits'],'entered':True,'fought':True,'won':False,'entry_index':len(request['history'])})
                rows.append((result,None))
            future=Future();future.set_result(rows)
            batch=ev.collect_probes(spec,future,'incomplete')
            self.assertEqual(batch['outcomes'],[None]*len(rows))
            self.assertEqual(records[-1]['usable_probes'],0)
            probes.absorb(spec,batch['outcomes'])
        self.assertEqual(probes.snapshot()['tables'][0]['status'],'UNKNOWN')
        self.assertEqual(probes.tiers(),{})
    def test_arm_reduction_checks_edits_and_exact_replayed_prefix_length(self):
        probes=PairedCardProbes(True,1);spec=probes.observe(trajectory(),'real',0,TEMPLATE)[0]
        request=spec['requests'][1]
        result=trajectory();result.update(status='PROBE',synthetic=True,value=None,consumed=3,
            probe={'edits':request['probe']['edits'],'entered':True,'fought':True,'won':False,'entry_index':3})
        self.assertIsNotNone(paired_outcome(result,request))
        changed=copy.deepcopy(result);changed['probe']['edits']=[]
        self.assertIsNone(paired_outcome(changed,request))
        changed=copy.deepcopy(result);changed['consumed']=2
        self.assertIsNone(paired_outcome(changed,request))
    def test_failed_probe_work_is_counted_but_never_becomes_a_signal(self):
        probes=PairedCardProbes(True,1);spec=probes.observe(trajectory(),'real',0,TEMPLATE)[0]
        rows=[]
        for request in spec['requests']:
            decision={'status':'UNSUPPORTED','reason':'late host error','native_terminal_observed':False,'value':None,
                'advisor_metrics':{'searches':[{'expanded_nodes':100,'wall_us':250000}]},'performance':{'wall_us':700000}}
            rows.append((decision,None))
        future=Future();future.set_result(rows)
        ev=object.__new__(Evaluator);ev.started=perf_counter();records=[];ev.record=records.append
        batch=ev.collect_probes(spec,future,'failed')
        self.assertEqual(records[0]['usable_probes'],0)
        self.assertEqual(records[0]['expanded_combat_nodes'],400)
        self.assertEqual(records[0]['probe_search_seconds'],1.0)
        self.assertAlmostEqual(records[0]['probe_native_seconds'],2.8)
        probes.absorb(spec,batch['outcomes'],batch['workload'])
        report=probes.snapshot()
        self.assertEqual(report['expanded_combat_nodes'],400)
        self.assertEqual(report['tiers'],{})
        self.assertEqual(report['tables'][0]['status'],'UNKNOWN')
    def test_missing_worker_metrics_are_explicit_unknown_not_zero_work(self):
        probes=PairedCardProbes(True,1);spec=probes.observe(trajectory(),'real',0,TEMPLATE)[0]
        rows=[(None,'timeout')]*len(spec['labels'])
        workload=probe_workload(rows,len(spec['labels']))
        probes.absorb(spec,[None]*len(rows),workload)
        report=probes.snapshot()
        self.assertIsNone(report['expanded_combat_nodes'])
        self.assertEqual(report['measured_expanded_combat_nodes'],0)
        self.assertEqual(report['unknown_work_probes'],4)
        self.assertFalse(report['work_measurement_complete'])
        self.assertEqual(report['tiers'],{})


if __name__=='__main__':unittest.main()
