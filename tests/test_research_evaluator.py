"""FakePool/Future tests only. No worker process, SDK, installed DLL or solver."""
from copy import deepcopy
import hashlib,json
from concurrent.futures import Future
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock,patch
import unittest
from spire_exact.canonical import canonical
from spire_exact.mode1 import context
from spire_exact.planning.search import Evaluator,SearchConfig,measured_research_work,research_cost_summary

CTX=context('42','IRONCLAD',10,'all')
ADVISOR={'budget_ms':1000,'boss_budget_ms':2000,'nodes':60000,'binary_identity':{'fixture':'fixed'}}
GAME_SHA='0'*64
IDENTITY={'host_sha256':'pure-test-host','game_sha256':GAME_SHA}


def progress_fixture():
    raw=json.dumps({'schema_version':0,'discovered_relics':[],'encounter_stats':[]},separators=(',',':'))
    return {'schema':'spire-research-progress/v1','game_sha256':GAME_SHA,'native_identity':deepcopy(IDENTITY),
        'context':{k:CTX[k]for k in ('seed','character','ascension','unlocks')}|{
            'information':'full','objective':'whole_run_victory/v1'},
        'baseline_sha256':hashlib.sha256(raw.encode('utf-8')).hexdigest(),'baseline_snapshot':raw}


def native_result(reason=None):
    return {'status':'TERMINAL' if reason is None else 'UNSUPPORTED','reason':reason,'value':[0] if reason is None else None,
        'native_terminal_observed':False,'trace':[],'decision_evidence':[],'observation':{'hp':'0','floor':49,'act':2},
        'performance':{'wall_us':1000,'cpu_us':600,'counters':{'prefix_replayed_actions':3,'checkpoint_skipped_actions':2,'new_actions':4},
            'exclusive_stages':{'beam_search':{'us':700},'prefix_native_execute':{'us':100}}},
        'advisor_metrics':{'searches':[{'expanded_nodes':17,'wall_us':700}]}}


class FakePool:
    def __init__(self):
        self.inputs={'fixture_inputs':'fixed'};self.calls=[];self.probe_calls=[];self.validated=[]
        self.result=native_result();self.rows=[];self.baseline_calls=[]
    def research_progress_baseline(self,ctx,out,timeout):
        self.baseline_calls.append((deepcopy(ctx),out,timeout));return progress_fixture()
    def validate_inputs(self,request):self.validated.append(deepcopy(request))
    def submit(self,request,out,timeout,**options):
        self.calls.append((deepcopy(request),dict(options)))
        future=Future();future.set_result((deepcopy(self.result),deepcopy(IDENTITY)));return future
    def submit_probes(self,requests,out,timeout):
        self.probe_calls.append(deepcopy(requests));future=Future();future.set_result(deepcopy(self.rows));return future


def fixture(directory,**switches):
    pool=FakePool();cfg=SearchConfig(**switches)
    ev=Evaluator(pool,CTX,Path(directory),cfg,ADVISOR)
    ev.identity=deepcopy(IDENTITY)
    ev.checkpoints=SimpleNamespace(nearest=Mock(return_value=SimpleNamespace(path=Path(directory)/'cp.json',prefix_length=2)),import_result=Mock())
    ev.cache=SimpleNamespace(get=Mock(return_value=None),put=Mock())
    ev.record=ev.records.append
    return ev,pool


def prefeature_request(prefix):
    # Frozen pre-feature reference 2da107e, Evaluator.request: this literal is
    # independent of the current function, not two calls to the same code.
    return {'seed':'42','character':'IRONCLAD','ascension':10,'unlocks':'all','history':deepcopy(prefix),
        'generate_candidate':True,'policy_seed':0,'max_decisions':2000,'capture_checkpoints':True,'low_io':False,
        'include_campaign_metadata':True,'advisor':{**deepcopy(ADVISOR),'fix_consumed_block_compensation':True}}


class ResearchDispatchTests(unittest.TestCase):
    def test_off_request_matches_independent_pre_feature_reference_bytes(self):
        prefix=[{'kind':'map','col':0,'row':1}]
        with TemporaryDirectory()as directory:
            for switches in ({},{'f1_winner_reuse':False,'real_card_menu_probes':False}):
                ev,pool=fixture(directory,**switches)
                actual=ev.request(prefix);self.assertEqual(canonical(actual),canonical(prefeature_request(prefix)))
                ev.dispatch([{'kind':'macro_exploration','request':actual}])
                expected=prefeature_request(prefix);expected['checkpoint']=str(Path(directory)/'cp.json')
                self.assertEqual(canonical(pool.calls[0][0]),canonical(expected));self.assertEqual(pool.calls[0][1],{})
                self.assertEqual(ev.cache.get.call_count,1);self.assertEqual(ev.checkpoints.nearest.call_count,1)
                self.assertNotIn('capture_f1_winners',actual);self.assertNotIn('capture_card_menu_state',actual)
                self.assertNotIn('research_progress',actual);self.assertEqual(pool.baseline_calls,[])

    def test_capture_sources_retain_persistent_cache_checkpoint_and_dynamic_prior(self):
        for switches in ({'f1_winner_reuse':True},{'real_card_menu_probes':True},
                         {'f1_winner_reuse':True,'real_card_menu_probes':True}):
            with self.subTest(switches=switches),TemporaryDirectory()as directory,patch('spire_exact.planning.search.runtime_snapshot',return_value={}):
                ev,pool=fixture(directory,**switches)
                ev.prior_provider=lambda serial:{'card:ONLINE':[0,0,3]}
                request=ev.request([{'kind':'map','col':1,'row':1}])
                ev.dispatch([{'kind':'macro_exploration','request':request}]);ev.collect()
                sent,options=pool.calls[0]
                self.assertEqual(options,{});self.assertTrue(sent['capture_checkpoints']);self.assertIn('checkpoint',sent)
                self.assertEqual(sent['policy_prior'],{'card:ONLINE':[0,0,3]})
                self.assertEqual(sent['research_progress'],progress_fixture());self.assertEqual(len(pool.baseline_calls),1)
                self.assertEqual(ev.cache.get.call_count,1);self.assertEqual(ev.cache.put.call_count,1)
                self.assertEqual(ev.checkpoints.nearest.call_count,1)
                record=ev.records[0];self.assertFalse(record['research_work']['isolated'])
                self.assertEqual(record['checkpoint_prefix'],2)

    def test_source_cache_hit_does_not_bill_original_native_work_twice(self):
        with TemporaryDirectory()as directory:
            ev,pool=fixture(directory,real_card_menu_probes=True)
            ev.cache.get.return_value=(native_result(),IDENTITY)
            ev.dispatch([{'kind':'macro_exploration','request':ev.request([])}]);ev.collect()
            self.assertEqual(pool.calls,[]);self.assertEqual(ev.checkpoints.nearest.call_count,0)
            self.assertEqual(ev.records[0]['research_work']['metrics']['native_wall_us'],0)
            self.assertTrue(ev.records[0]['research_work']['cache_hit'])

    def test_consumers_are_fresh_and_do_not_resample_source_prior_or_advisor(self):
        for kind,field in (('f1_winner_reuse','f1_winner_proposal'),('real_card_menu_followup','real_card_menu_choice')):
            for prior in (None,{'card:SOURCE':[0,0,-2]}):
                with self.subTest(kind=kind,prior=prior),TemporaryDirectory()as directory,patch('spire_exact.planning.search.runtime_snapshot',return_value={}):
                    ev,pool=fixture(directory,f1_winner_reuse=True,real_card_menu_probes=True)
                    provider=Mock(return_value={'card:NEW':[0,0,8]});ev.prior_provider=provider
                    request=ev.request([]);request.update(policy_seed=912,checkpoint='unsafe',capture_checkpoints=True)
                    request[field]={'fixture':'source_guard'}
                    request['advisor']['gate_plans']={'FinalBoss':{'members':[{'beam':405,'nodes':600000}]}}
                    if prior is not None:request['policy_prior']=deepcopy(prior)
                    ev.dispatch([{'kind':kind,'request':request}]);ev.collect()
                    sent,options=pool.calls[0]
                    self.assertEqual(options,{'fresh':True,'disposable':True});self.assertFalse(sent['capture_checkpoints'])
                    self.assertNotIn('checkpoint',sent);self.assertEqual(sent['policy_seed'],912)
                    self.assertEqual(sent.get('policy_prior'),prior);self.assertFalse(provider.called)
                    self.assertEqual(sent['advisor']['gate_plans'],request['advisor']['gate_plans'])
                    self.assertEqual(sent['research_progress'],progress_fixture())
                    self.assertFalse(ev.cache.get.called);self.assertFalse(ev.cache.put.called);self.assertFalse(ev.checkpoints.nearest.called)
                    self.assertTrue(ev.records[0]['research_work']['isolated'])
                    self.assertFalse(ev.checkpoints.import_result.called)

    def test_isolated_consumer_cannot_import_checkpoints_from_malicious_native_result(self):
        with TemporaryDirectory()as directory,patch('spire_exact.planning.search.runtime_snapshot',return_value={}):
            ev,pool=fixture(directory,f1_winner_reuse=True)
            pool.result['checkpoints']=[{'path':'untrusted-proposal-cp.json'}]
            request=ev.request([]);request['f1_winner_proposal']={'fixture':True}
            ev.dispatch([{'kind':'f1_winner_reuse','request':request}]);ev.collect()
            self.assertFalse(ev.checkpoints.import_result.called)
            self.assertFalse(ev.cache.put.called)

    def test_source_baseline_payload_is_independent_between_requests(self):
        with TemporaryDirectory()as directory:
            ev,pool=fixture(directory,f1_winner_reuse=True)
            first=ev.request([]);first['research_progress']['baseline_snapshot']='polluted'
            second=ev.request([])
            self.assertEqual(second['research_progress'],progress_fixture())
            self.assertEqual(len(pool.baseline_calls),1)

    def test_synthetic_batch_never_reads_or_writes_real_cache_checkpoint(self):
        with TemporaryDirectory()as directory,patch('spire_exact.planning.search.runtime_snapshot',return_value={}):
            ev,pool=fixture(directory,real_card_menu_probes=True)
            pool.rows=[(None,'NATIVE_TASK_TIMEOUT')]
            spec={'kind':'real_card_menu_probe','request':{'history':[],'generate_candidate':False},
                'requests':[{'history':[],'card_menu_probe':{'fixture':True}}],
                'table':0,'sample':1,'labels':['skip'],'base':'source','gate':(2,'card_menu')}
            ev.dispatch([spec]);trials=ev.collect()
            self.assertEqual(pool.calls,[]);self.assertEqual(len(pool.probe_calls),1)
            self.assertFalse(ev.cache.get.called);self.assertFalse(ev.cache.put.called)
            self.assertFalse(ev.checkpoints.nearest.called);self.assertFalse(ev.checkpoints.import_result.called)
            batch=trials[0][1];self.assertTrue(batch['synthetic']);self.assertEqual(batch['trace'],[])
            self.assertEqual(batch['outcomes'],[None]);self.assertEqual(batch['rejections'],['NATIVE_TASK_TIMEOUT'])
            self.assertFalse(batch['workload']['work_measurement_complete'])
            self.assertIsNone(ev.records[0]['expanded_combat_nodes'])

    def test_unknown_real_consumer_retains_reason_and_unknown_spent_cost(self):
        with TemporaryDirectory()as directory,patch('spire_exact.planning.search.runtime_snapshot',return_value={}):
            ev,pool=fixture(directory,real_card_menu_probes=True)
            pool.result={'status':'UNSUPPORTED','reason':'CHECKPOINT_MISMATCH:card_menu_source_native_state',
                         'trace':[],'value':None,'native_terminal_observed':False}
            request=ev.request([]);request['real_card_menu_choice']={'fixture':True}
            ev.dispatch([{'kind':'real_card_menu_followup','request':request}]);ev.collect()
            summary=research_cost_summary(ev.records)['groups']['consumer_B']
            self.assertEqual(summary['reasons'],{'CHECKPOINT_MISMATCH:card_menu_source_native_state':1})
            self.assertEqual(summary['metrics']['native_wall_us']['unknown_evaluations'],1)
            self.assertIsNone(ev.records[0]['research_work']['metrics']['native_wall_us'])


class ResearchCostTests(unittest.TestCase):
    def test_reported_costs_and_missing_reports_are_distinct(self):
        measured=measured_research_work(native_result(),submit_to_absorb_seconds=.5)
        self.assertEqual(measured['metrics']['native_wall_us'],1000)
        self.assertEqual(measured['metrics']['expanded_combat_nodes'],17)
        self.assertEqual(measured['metrics']['prefix_replayed_actions'],3)
        self.assertEqual(measured['missing_metrics'],[])
        missing=measured_research_work({'status':'UNKNOWN'})
        self.assertTrue(all(value is None for value in missing['metrics'].values()))
        self.assertIsNone(missing['submit_to_absorb_seconds'])
        summary=research_cost_summary([{'research_work':{'role':'consumer_A','isolated':True,**measured}},
            {'reason':'worker crashed','research_work':{'role':'consumer_A','isolated':True,**missing}}])
        field=summary['groups']['consumer_A']['metrics']['native_wall_us']
        self.assertEqual(field,{'measured_sum':1000,'unknown_evaluations':1})

    def test_partial_synthetic_work_preserves_measured_subset_and_unknown_count(self):
        record={'research_work':{'role':'synthetic_B','isolated':True,'cache_hit':False,
            'metrics':{'expanded_combat_nodes':None},'measured_subsets':{'expanded_combat_nodes':19},
            'submit_to_absorb_seconds':None}}
        summary=research_cost_summary([record])['groups']['synthetic_B']
        self.assertEqual(summary['metrics']['expanded_combat_nodes'],{'measured_sum':19,'unknown_evaluations':1})
        self.assertEqual(summary['submit_interval_unknown'],1)
