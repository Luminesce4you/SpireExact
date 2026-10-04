"""Pure protocol tests: no installed game, native worker or solver is started."""
from copy import deepcopy
import unittest
from spire_exact.canonical import canonical
from spire_exact.planning.real_card_menu_probes import RealCardMenuProbes,menu_probe_outcome,score,valid_menu_outcome
from spire_exact.planning.paired_card_probes import MAP_OBSERVATION_KEYS,SAMPLES

TEMPLATE={'seed':'42','character':'IRONCLAD','ascension':10,'unlocks':'all','history':[],
    'generate_candidate':True,'capture_checkpoints':True,'checkpoint':'old.json','capture_card_menu_state':True,
    'capture_f1_winners':True,'policy_seed':91,'policy_prior':{'paired:card:A':[0,0,2]},'max_decisions':12000,
    'advisor':{'nodes':60000,'budget_ms':600000,'boss_budget_ms':600000,
        'gate_plans':{'FinalBoss':{'members':[{'mode':'Evaluate','beam':270,'nodes':120000}]}}}}
STATE={'run':{'native_json':'{"deck":["STRIKE"],"count":1}'},'progress':{'native_json':'{"discovered":["A"]}'},
    'action_runtime':{'executor_running':True,'next_action_id':4},'reward_sync':{'set_id':3,'cards':['{"id":"A"}']}}
MENU=[{'kind':'card_reward','index':0,'card':'A'},{'kind':'card_reward','index':1,'card':'B'},{'kind':'card_skip'}]


def trajectory(serial=0,chosen=0):
    obs={key:None for key in MAP_OBSERVATION_KEYS}
    obs.update(act=2,floor=41,hp='40',max_hp='80',gold=12,deck=[{'id':'STRIKE','upgrade':0}],
        rng={'Shuffle':{'state':serial,'calls':3}},character='IRONCLAD',room='Monster',block='0',
        relics=[],potions=[],score=0)
    trace=[{'kind':'map','col':serial,'row':6},deepcopy(MENU[chosen])]
    return {'trace':trace,'decision_evidence':[{'phase':'map','observation':{}},
        {'phase':'card_reward','observation':obs,'available_actions':deepcopy(MENU)}],
        'campaign':{'act_count':3,'final_act_boss_count':2},
        'card_menu_sources':[{'index':1,'source_native_state':deepcopy(STATE)}]}


def probe_result(request,stage=0,won=False,pre_gate=False):
    history=deepcopy(request['history']);spec=request['card_menu_probe'];choice=deepcopy(spec['choice'])
    entry={'act':2,'floor':49 if stage==0 else 50,'max_hp':'80'}
    obs={'act':2,'floor':entry['floor'],'hp':'20' if won else '0'}
    combat={'act':2,'floor':entry['floor'],'turn':1,'enemies':[{'id':'BOSS','combat_id':1,'hp':'100','max_hp':'100'}]}
    trace=history+[choice,{'kind':'end_turn'}]
    evidence=[{'phase':'map','observation':{}}for _ in history]+[
        {'phase':'card_reward','observation':deepcopy(spec['expected_entry_observation'])},
        {'phase':'combat','observation':combat}]
    info={'menu_checked':True,'native_finished':True,'choice_index':len(history),'native_won':won,
        'choice':choice,'source_observation':deepcopy(spec['expected_entry_observation']),
        'source_actions':deepcopy(spec['expected_actions']),'source_native_state':deepcopy(spec['expected_source_native_state']),
        'rng':spec['rng'],'sample_applied':not pre_gate,'f1_won':stage==1 or won,'f2_won':won,
        'f1_entry':None if pre_gate else {'act':2,'floor':49,'max_hp':'80'},
        'f2_entry':{'act':2,'floor':50,'max_hp':'80'}if stage==1 or won else None}
    return {'schema':'spire-native-card-menu-probe/v1','status':'CARD_MENU_PROBE','synthetic':True,'reason':None,
        'value':None,'native_terminal_observed':False,'consumed':len(history),'card_menu_probe':info,
        'trace':trace,'decision_evidence':evidence,'observation':obs,
        'terminal_combat':{'act':2,'floor':entry['floor'],'turn':2,
            'enemies':[{'id':'BOSS','combat_id':1,'hp':'40','max_hp':'100'}]}}


def loss(stage=0,removed=60,total=100):return {'won':False,'stage':stage,'hp_fraction':0.0,'lost':[removed,total]}


class RealMenuTables(unittest.TestCase):
    def make(self):return RealCardMenuProbes(True,123,context={'seed':'42'},inputs={'game':'fixed'})
    def test_default_off_and_native_context_deferral(self):
        self.assertEqual(RealCardMenuProbes().observe(trajectory(),'x',0,TEMPLATE),[])
        for patch in ({'character':'SILENT'},{'ascension':0},{'unlocks':'none'},{'advisor':None}):
            p=self.make();self.assertEqual(p.observe(trajectory(),'x',0,{**TEMPLATE,**patch}),[])
            self.assertIsNotNone(p.snapshot()['inactive_reason'])
        bad=trajectory();bad['campaign']['final_act_boss_count']=1
        self.assertEqual(self.make().observe(bad,'x',0,TEMPLATE),[])

    def test_thresholds_and_exact_duplicate_menu(self):
        p=self.make();scheduled=[]
        for index in range(1,513):
            if p.observe(trajectory(index),str(index),0,TEMPLATE):scheduled.append(index)
        self.assertEqual(scheduled,[1,32,128,512]);self.assertEqual(p.next_threshold,2048)
        self.assertEqual(p.observe(trajectory(512),'same',0,TEMPLATE),[])
        self.assertEqual(len(p.seen),512)

    def test_skip_first_real_actions_and_sample_request_isolation(self):
        source=trajectory();p=self.make();specs=p.observe(source,'source',0,TEMPLATE,'pick')
        self.assertEqual(len(specs),SAMPLES)
        for batch in specs:
            self.assertEqual(batch['requests'][0]['card_menu_probe']['choice'],{'kind':'card_skip'})
            self.assertEqual([r['card_menu_probe']['choice']for r in batch['requests'][1:]],MENU[:2])
            self.assertEqual({r['card_menu_probe']['rng']for r in batch['requests']},{batch['sample']})
            for request in batch['requests']:
                self.assertFalse(request['capture_checkpoints']);self.assertEqual(request['history'],source['trace'][:1])
                for field in ('checkpoint','capture_f1_winners','capture_card_menu_state','real_card_menu_choice','expected_evidence'):
                    self.assertNotIn(field,request)
                self.assertNotIn('hp',request['card_menu_probe']);self.assertNotIn('edits',request['card_menu_probe'])
                self.assertEqual(request['advisor'],TEMPLATE['advisor']);canonical(request)
        specs[0]['requests'][0]['card_menu_probe']['expected_source_native_state']['run']['native_json']='altered'
        self.assertEqual(source['card_menu_sources'][0]['source_native_state'],STATE)
        self.assertEqual(specs[0]['requests'][1]['card_menu_probe']['expected_source_native_state'],STATE)

    def test_source_bytes_observation_context_and_native_state_bind_identity(self):
        p=self.make();source=trajectory();p.observe(source,'base',0,TEMPLATE)
        for edit in ('prefix','observation','native_state'):
            changed=deepcopy(source)
            if edit=='prefix':changed['trace'][0]['extra']=1
            elif edit=='observation':changed['decision_evidence'][1]['observation']['rng']['Shuffle']['calls']=4
            else:changed['card_menu_sources'][0]['source_native_state']['progress']['native_json']='{"discovered":["B"]}'
            p.observe(changed,edit,0,TEMPLATE)
        self.assertEqual(len(p.seen),4)
        p.observe(source,'different_native_seed',0,{**TEMPLATE,'seed':'43'})
        self.assertEqual(len(p.seen),5)
        context=self.make();context.context={'seed':'43'}
        context.observe(source,'x',0,TEMPLATE)
        baseline=self.make();baseline.observe(source,'x',0,TEMPLATE)
        self.assertNotEqual(next(iter(context.seen)),next(iter(baseline.seen)))
        bad=deepcopy(source);bad['card_menu_sources'][0]['source_native_state']=None;bad['card_menu_sources'][0]['error']='SerializerGap'
        q=self.make();self.assertEqual(q.observe(bad,'gap',0,TEMPLATE),[])
        self.assertEqual(q.snapshot()['source_guard_issues'],{'SerializerGap':1})
        self.assertEqual(q.observe(source,'replayed',2,TEMPLATE),[])

    def test_only_complete_paired_table_can_propose_unsampled_real_followup(self):
        p=self.make();specs=p.observe(trajectory(chosen=0),'base',0,TEMPLATE,'pick')
        for spec in specs[:4]:self.assertEqual(p.absorb(spec,[loss(removed=10),loss(removed=20),loss(removed=80)]),[])
        self.assertEqual(p.tables[0]['status'],'UNKNOWN')
        jobs=p.absorb(specs[4],[loss(removed=10),loss(removed=20),loss(removed=80)])
        self.assertEqual(len(jobs),1);request=jobs[0]['request']
        self.assertEqual(jobs[0]['family'],'pick');self.assertEqual(jobs[0]['kind'],'real_card_menu_followup')
        self.assertEqual(request['real_card_menu_choice']['choice'],MENU[1]);self.assertNotIn('card_menu_probe',request)
        self.assertNotIn('checkpoint',request);self.assertFalse(request['capture_checkpoints'])
        self.assertEqual(request['policy_seed'],TEMPLATE['policy_seed']);self.assertEqual(request['policy_prior'],TEMPLATE['policy_prior'])
        self.assertEqual(request['advisor'],TEMPLATE['advisor']);canonical(request)
        self.assertEqual(p.snapshot()['real_rollouts_proposed'],1)

    def test_best_is_original_action_does_not_schedule_duplicate_rollout(self):
        p=self.make();specs=p.observe(trajectory(chosen=1),'base',0,TEMPLATE)
        for spec in specs:self.assertEqual(p.absorb(spec,[loss(removed=10),loss(removed=20),loss(removed=80)]),[])
        self.assertEqual(p.tables[0]['status'],'PAIRED_SIGNAL');self.assertEqual(p.real_proposals,0)

    def test_missing_or_invalid_arm_is_unknown_and_reports_reason(self):
        for invalid in (None,{}, {'won':True,'stage':0,'hp_fraction':1,'lost':None},loss(total=0),loss(removed=101),
                        {'won':True,'stage':2,'hp_fraction':float('nan'),'lost':None}):
            p=self.make();specs=p.observe(trajectory(),'base',0,TEMPLATE)
            for spec in specs:
                self.assertEqual(p.absorb(spec,[loss(),invalid,loss()],rejections=[None,'GUARD_MISMATCH',None]),[])
            self.assertEqual(p.tables[0]['status'],'UNKNOWN');self.assertEqual(p.errors,5)
            self.assertEqual(p.snapshot()['rejection_reasons'],{'GUARD_MISMATCH':5})
            self.assertFalse(p.snapshot()['work_measurement_complete'])
        q=self.make();spec=q.observe(trajectory(),'base',0,TEMPLATE)[0]
        q.absorb(spec,[loss()]);self.assertEqual(q.errors,2)
        with self.assertRaises(ValueError):q.absorb(spec,[loss(),loss(),loss()])

    def test_scoring_keeps_f1_f2_and_whole_win_distinct_and_common_denominator(self):
        totals={0:200,1:400}
        self.assertEqual(score(loss(0,100,100),totals),0.5)
        self.assertEqual(score(loss(1,100,100),totals),1.25)
        self.assertEqual(score({'won':False,'stage':0,'hp_fraction':0,'lost':None},totals),0)
        self.assertEqual(score({'won':True,'stage':2,'hp_fraction':.25,'lost':None},totals),2.25)
        self.assertFalse(valid_menu_outcome({'won':False,'stage':True,'hp_fraction':0,'lost':None}))


class OutcomeContract(unittest.TestCase):
    def request(self):return RealCardMenuProbes(True).observe(trajectory(),'base',0,TEMPLATE)[0]['requests'][1]
    def test_native_loss_and_full_chain_win_and_pre_f1_death(self):
        request=self.request()
        for stage in (0,1):
            outcome=menu_probe_outcome(probe_result(request,stage),request)
            self.assertEqual(outcome['stage'],stage);self.assertEqual(outcome['lost'],[60.0,100.0])
        outcome=menu_probe_outcome(probe_result(request,1,won=True),request)
        self.assertTrue(outcome['won']);self.assertEqual(outcome['hp_fraction'],.25)
        pre=probe_result(request,pre_gate=True)
        self.assertFalse(pre['card_menu_probe']['sample_applied'])
        self.assertEqual(menu_probe_outcome(pre,request),{'won':False,'stage':0,'hp_fraction':0.0,'lost':None})

    def test_rejects_outer_contract_missing_or_malformed_report(self):
        request=self.request();base=probe_result(request)
        self.assertIsNone(menu_probe_outcome(None,request))
        for change in ({'schema':'other'},{'status':'UNSUPPORTED'},{'synthetic':False},{'reason':'timeout'},
                       {'value':[1]},{'native_terminal_observed':True},{'consumed':True},
                       {'card_menu_probe':[]},{'trace':[]},{'decision_evidence':[]}):
            with self.subTest(change=change):self.assertIsNone(menu_probe_outcome({**deepcopy(base),**change},request))

    def test_rejects_menu_prefix_instance_rng_and_full_guard_mismatch(self):
        request=self.request();base=probe_result(request)
        changes=({'menu_checked':False},{'native_finished':False},{'choice_index':True},{'native_won':None},
                 {'rng':-1},{'choice':{'kind':'card_skip'}},{'source_actions':[]},{'source_observation':{}},{'source_native_state':{}})
        for changed in changes:
            result=deepcopy(base);result['card_menu_probe'].update(changed)
            self.assertIsNone(menu_probe_outcome(result,request))
        for where in ('history','selected_action'):
            result=deepcopy(base);result['trace'][0 if where=='history'else 1]={'kind':'different'}
            self.assertIsNone(menu_probe_outcome(result,request))

    def test_unknown_combat_progress_and_incomplete_winning_chain_are_rejected(self):
        request=self.request()
        for change in ({'terminal_combat':None},{'observation':{'act':2,'floor':1,'hp':'0'}},
                       {'observation':{'act':2,'floor':49,'hp':'nan'}}):
            result=probe_result(request);result.update(change);self.assertIsNone(menu_probe_outcome(result,request))
        for changed in ({'f1_won':False},{'f2_won':False},{'sample_applied':False},{'f2_entry':None}):
            result=probe_result(request,1,won=True);result['card_menu_probe'].update(changed)
            self.assertIsNone(menu_probe_outcome(result,request))
