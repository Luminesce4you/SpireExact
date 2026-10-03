"""Unit tests for orchestration; actual-DLL evidence lives in validate_p5_native.py."""
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from spire_exact.canonical import canonical
from spire_exact.planning.archive import (CheckpointArchive,ResultCache,DiverseFrontier,decision_groups,
                                         utility,classify_failure,failure_combat_prefix)
from spire_exact.planning.resources import ResourcePlan,MIB,effective_cpus
from spire_exact.planning.search import SearchConfig,Evaluator
from spire_exact.planning.io import read_json,write_json
from spire_exact.mode1 import context,is_winning_candidate

CTX=context('42','IRONCLAD',0,'all')
ID={'host_sha256':'h','game_sha256':'g'}

def candidate(hp='20',floor=4,status='BUDGET',potions=None,trace=None):
    return {'status':status,'value':[0] if status=='TERMINAL' else None,
        'trace':trace or [{'kind':'map','col':1,'row':1}],
        'observation':{'hp':hp,'max_hp':'80','floor':floor,'act':0,'gold':30,
                       'potions':potions or [],'deck':[], 'relics':[]},'decision_evidence':[]}

class Checkpoints(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.archive=CheckpointArchive(CTX,ID)
    def tearDown(self):self.temp.cleanup()
    def write(self,name,trace,**overrides):
        p={'context':{**{k:CTX[k] for k in ('seed','character','ascension','unlocks')},'information':'full'},
           'identity':ID,'history':trace,'evidence':[{} for _ in trace], 'observation':{'act':0,'floor':len(trace)}}
        p.update(overrides);path=self.root/name
        write_json(path,{'schema':'spire-map-checkpoint/v1','payload':p,'sha256':'native-checks-this'})
        return path
    def test_longest_exact_prefix(self):
        self.archive.add(self.write('a.json',[{'a':1}]))
        self.archive.add(self.write('b.json',[{'a':1},{'b':2}]))
        self.assertEqual(self.archive.nearest([{'a':1},{'b':2},{'c':3}]).prefix_length,2)
    def test_branch_never_restores_future(self):
        self.archive.add(self.write('a.json',[{'a':1}]))
        self.archive.add(self.write('b.json',[{'a':1},{'b':2}]))
        self.assertEqual(self.archive.nearest([{'a':1},{'b':9}]).prefix_length,1)
    def test_different_seed_rejected(self):
        path=self.write('a.json',[],context={'seed':'other'})
        self.assertFalse(self.archive.add(path))
    def test_different_version_rejected(self):
        self.assertFalse(self.archive.add(self.write('a.json',[],identity={'host_sha256':'changed'})))
    def test_unknown_schema_rejected(self):
        path=self.write('a.json',[]);d=read_json(path);d['schema']='future';write_json(path,d)
        self.assertFalse(self.archive.add(path))
    def test_missing_evidence_rejected(self):
        self.assertFalse(self.archive.add(self.write('a.json',[{'a':1}],evidence=[])))
    def test_array_order_matters(self):
        self.archive.add(self.write('a.json',[{'indices':[0,1]}]))
        self.assertIsNone(self.archive.nearest([{'indices':[1,0]}]))
    def test_eviction_never_deletes_file(self):
        a=self.write('a.json',[{'a':1}]);b=self.write('b.json',[{'b':2}])
        self.archive.byte_limit=max(a.stat().st_size,b.stat().st_size)
        self.archive.add(a);self.archive.add(b)
        self.assertTrue(a.exists());self.assertTrue(b.exists());self.assertEqual(len(self.archive.entries),1)
    def test_too_large_not_indexed(self):
        self.archive.byte_limit=1;self.assertFalse(self.archive.add(self.write('a.json',[])))
    def test_missing_file_not_used(self):
        path=self.write('a.json',[{'a':1}]);self.archive.add(path);path.unlink()
        self.assertIsNone(self.archive.nearest([{'a':1}]))

class CacheTests(unittest.TestCase):
    def test_key_contains_full_request(self):
        c=ResultCache();c.put(canonical({'seed':'1','a':[1,2]}),{'x':1})
        self.assertIsNone(c.get(canonical({'seed':'1','a':[2,1]})))
    def test_mutation_isolated(self):
        c=ResultCache();v={'a':[1]};c.put(b'k',v);v['a'].append(2)
        x=c.get(b'k');x['a'].append(3);self.assertEqual(c.get(b'k'),{'a':[1]})
    def test_memory_bounded(self):
        c=ResultCache(100)
        for i in range(100):c.put(str(i).encode(),{'x':'z'*20})
        self.assertLessEqual(c.bytes,100)
    def test_old_key_invalidated_by_rules(self):
        c=ResultCache();c.put(canonical({'rule':'old','state':1}),{'v':1})
        self.assertIsNone(c.get(canonical({'rule':'new','state':1})))
    def test_cache_counts_hits_only(self):
        c=ResultCache();self.assertIsNone(c.get(b'a'));c.put(b'a',{});c.get(b'a');self.assertEqual(c.hits,1)

class Resources(unittest.TestCase):
    def detect(self,**kwargs):
        with patch('spire_exact.planning.resources.effective_cpus',return_value=8),patch('spire_exact.planning.resources.available_memory',return_value=8*1024*MIB):
            return ResourcePlan.detect(**kwargs)
    def test_reserve_cpu(self):self.assertEqual(self.detect().workers,7)
    def test_nested_dop_bounded(self):self.assertLessEqual(self.detect(dop=3).workers*3,7)
    def test_clamp_requested_workers(self):self.assertEqual(self.detect(workers=100).workers,8)
    def test_explicit_eighth_slot_still_obeys_memory_and_dop(self):
        self.assertEqual(self.detect(workers=8,worker_mib=1536,reserve_mib=1024).workers,4)
        self.assertLessEqual(self.detect(workers=8,dop=3).workers*3,8)
    def test_reserve_memory(self):
        with patch('spire_exact.planning.resources.effective_cpus',return_value=32),patch('spire_exact.planning.resources.available_memory',return_value=2500*MIB):
            self.assertEqual(ResourcePlan.detect(worker_mib=900,reserve_mib=512).workers,2)
    def test_low_memory_fails_before_launch(self):
        with patch('spire_exact.planning.resources.available_memory',return_value=100*MIB):
            with self.assertRaises(MemoryError):ResourcePlan.detect()
    def test_invalid_dop(self):
        with self.assertRaises(ValueError):self.detect(dop=0)
    def test_invalid_workers(self):
        with self.assertRaises(ValueError):self.detect(workers=0)
    def test_invalid_memory(self):
        with self.assertRaises(ValueError):self.detect(worker_mib=1)
    def test_actual_cpu_positive(self):self.assertGreater(effective_cpus(),0)

class Planning(unittest.TestCase):
    def test_route_death_not_unsat(self):self.assertEqual(classify_failure(candidate(status='TERMINAL')),'NATIVE_ROUTE_DEATH')
    def test_budget_not_death(self):self.assertEqual(classify_failure(candidate()),'SEARCH_BUDGET')
    def test_checkpoint_gap_separate(self):self.assertEqual(classify_failure({'reason':'CHECKPOINT_MISMATCH','status':'UNSUPPORTED'}),'RESTORE_OR_REPLAY_MISMATCH')
    def test_timeout_separate(self):self.assertEqual(classify_failure({'reason':'NATIVE_TASK_TIMEOUT'}),'TIMEOUT')
    def test_memory_separate(self):self.assertEqual(classify_failure({'reason':'MEMORY_ADMISSION_DENIED'}),'RESOURCE_LIMIT')
    def test_mechanism_gap_separate(self):self.assertEqual(classify_failure({'status':'UNSUPPORTED'}),'MECHANISM_OR_HOST_GAP')
    def test_fake_win_not_proof(self):self.assertFalse(is_winning_candidate({'status':'TERMINAL','value':[1]}))
    def test_alive_frontier_preferred_to_farther_death(self):self.assertGreater(utility(candidate()),utility(candidate('0',20,'TERMINAL')))
    def test_quality_not_card_ids(self):
        a=candidate();b=candidate();a['observation']['character']='IRONCLAD';b['observation']['character']='UNKNOWN_FUTURE_CHARACTER'
        self.assertEqual(utility(a),utility(b))
    def test_current_numbers_used(self):self.assertGreater(utility(candidate('60')),utility(candidate('20')))
    def test_diverse_frontier_keeps_resources(self):
        f=DiverseFrontier();f.add(candidate('60',trace=[{'a':1}]),'a');f.add(candidate('20',potions=['P'],trace=[{'a':2}]),'b')
        self.assertEqual(len(f.rows),2)
    def test_frontier_cap(self):
        f=DiverseFrontier(limit=3)
        for i in range(20):f.add(candidate(floor=i,trace=[{'a':i}]),str(i))
        self.assertEqual(len(f.rows),3)
    def test_same_trace_no_duplicate(self):
        f=DiverseFrontier();f.add(candidate(),'a');f.add(candidate(),'b');self.assertEqual(len(f.rows),1)
    def test_configuration_rejects_zero(self):
        with self.assertRaises(ValueError):SearchConfig(evaluations=0)
    def test_telemetry_floats_separate_from_exact_keys(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'a.json';write_json(p,{'seconds':.3});self.assertEqual(read_json(p)['seconds'],.3)
        with self.assertRaises(ValueError):canonical({'state':.3})
    def test_atomic_json_roundtrip(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'a.json';write_json(p,{'a':1});self.assertFalse(p.with_name('a.json.tmp').exists())
    def test_group_replays_only_prefix_before_mutation(self):
        r={'trace':[{'a':1},{'a':2},{'a':3}], 'decision_evidence':[
            {'phase':'map','observation':{'floor':1},'available_actions':[{'a':1},{'a':9}]},
            {'phase':'combat','available_actions':[{'a':2}]},{'phase':'combat','available_actions':[{'a':3}]}]}
        g=decision_groups(r,1,set())[0];self.assertEqual(g['prefixes'],[[{'a':1}],[{'a':9}]])
    def test_probe_targets_native_death_entry(self):
        r=candidate('0',4,'TERMINAL',trace=[{'a':0},{'a':1}]);r['decision_evidence']=[
            {'phase':'map','observation':{'floor':3}}, {'phase':'combat','observation':{'floor':4}}]
        self.assertEqual(failure_combat_prefix(r)['prefix'],[{'a':0}])
    def test_probe_does_not_target_unsupported(self):self.assertIsNone(failure_combat_prefix({'status':'UNSUPPORTED'}))
    def test_repair_window_expands_to_earlier_choices(self):
        r={'trace':[{'a':i} for i in range(5)],'decision_evidence':[{'phase':'card_reward','observation':{'floor':i},
           'available_actions':[{'a':i},{'b':i}]} for i in range(5)]}
        seen=set();self.assertEqual(decision_groups(r,1,seen)[0]['index'],4)
        self.assertEqual(decision_groups(r,3,seen)[0]['index'],3)
    def test_cap_explicitly_deferred(self):
        r={'trace':[{'a':0}],'decision_evidence':[{'phase':'shop','available_actions':[{'a':i} for i in range(10)]}]}
        self.assertEqual(decision_groups(r,1,set(),limit=3)[0]['deferred_actions'],7)

def _phase_test(phase):
    def test(self):
        r={'trace':[{'kind':'a'}],'decision_evidence':[{'phase':phase,'observation':{'floor':5},
            'available_actions':[{'kind':'a'},{'kind':'b'}]}]}
        g=decision_groups(r,1,set())[0]
        self.assertEqual(len(g['prefixes']),2);self.assertEqual(g['phase'],phase)
    return test
for _phase in ('map','shop','rest','card_reward','event','select_cards','rewards','treasure'):
    setattr(Planning,'test_lookahead_'+_phase,_phase_test(_phase))

class Hardening(unittest.TestCase):
    def test_dop_itself_is_clamped(self):
        with patch('spire_exact.planning.resources.effective_cpus',return_value=4),patch('spire_exact.planning.resources.available_memory',return_value=8000*MIB):
            plan=ResourcePlan.detect(workers=8,dop=100)
            self.assertLessEqual(plan.workers*plan.dop,4)
    def test_one_fractional_core_still_one_lane(self):
        with patch('spire_exact.planning.resources.effective_cpus',return_value=.5),patch('spire_exact.planning.resources.available_memory',return_value=8000*MIB):
            plan=ResourcePlan.detect(workers=8,dop=8)
            self.assertEqual((plan.workers,plan.dop),(1,1))
    def test_advisor_update_same_path_invalidates(self):
        from spire_exact.planning.identity import advisor_identity,require_advisor_identity
        with tempfile.TemporaryDirectory() as d:
            dll=Path(d)/'solver.dll';dll.write_bytes(b'old')
            cfg={'solver':str(dll),'harness':str(dll),'dependency_dirs':[]}
            cfg['binary_identity']=advisor_identity(cfg);require_advisor_identity(cfg)
            dll.write_bytes(b'new')
            with self.assertRaises(ValueError):require_advisor_identity(cfg)
    def test_advisor_identity_cannot_be_absent(self):
        from spire_exact.planning.identity import require_advisor_identity
        with self.assertRaises(ValueError):require_advisor_identity({})

class ArchiveHardening(unittest.TestCase):
    setUp=Checkpoints.setUp
    tearDown=Checkpoints.tearDown
    write=Checkpoints.write
    def test_missing_zero_prefix_not_used(self):
        path=self.write('a.json',[]);self.archive.add(path);path.unlink()
        self.assertIsNone(self.archive.nearest([{'a':1}]))
    def test_eviction_prunes_empty_tree(self):
        for i in range(100):
            p=self.write(f'{i}.json',[{'first':i},{'last':i}]);self.archive.byte_limit=p.stat().st_size
            self.archive.add(p)
        self.assertLessEqual(len(self.archive.root),1)
    def test_oversize_never_allocates_branch(self):
        self.archive.byte_limit=1
        for i in range(20):self.archive.add(self.write(f'{i}.json',[{'a':i}]))
        self.assertEqual(self.archive.root,{})
    def test_same_path_reindexed_without_stale_branch(self):
        path=self.write('a.json',[{'a':1}]);self.archive.add(path)
        path=self.write('a.json',[{'a':2}]);self.archive.add(path)
        self.assertIsNone(self.archive.nearest([{'a':1}]))
        self.assertEqual(self.archive.nearest([{'a':2}]).prefix_length,1)
        self.assertEqual(len(self.archive.entries),1)

class LifecycleTests(unittest.TestCase):
    def test_close_broken_pipe_does_not_mask_failure(self):
        from spire_exact.planning.pool import NativeWorker
        from unittest.mock import Mock
        plan=ResourcePlan(1,1,1000*MIB,512*MIB,4,8000*MIB)
        worker=NativeWorker('dotnet',Path('x'),Path('y'),Path('z'),plan)
        process=Mock();process.poll.return_value=1;process.stdin.close.side_effect=BrokenPipeError()
        worker.process=process
        worker.close()
        self.assertIsNone(worker.process)
    def test_successful_probe_is_extended_before_repairing_old_choices(self):
        from time import perf_counter
        from types import SimpleNamespace
        from spire_exact.planning.search import solve
        requests=[]
        dead=candidate('0',4,'TERMINAL',trace=[{'a':0},{'a':1}])
        dead['decision_evidence']=[{'phase':'map','observation':{'floor':3},'available_actions':[{'a':0},{'a':9}]},
                                  {'phase':'combat','observation':{'floor':4},'available_actions':[{'a':1}]}]
        survived=candidate('40',4,trace=[{'a':0},{'a':2}])
        class FakeEvaluator(Evaluator):
            def __init__(self,pool,ctx,directory,config,advisor):
                self.pool,self.ctx,self.directory,self.config,self.advisor=pool,ctx,directory,config,advisor
                self.cache=SimpleNamespace(hits=0);self.checkpoints=None;self.identity=None
                self.serial=0;self.records=[];self.started=perf_counter();self.deadline=self.started+100;self.inflight={};self.ready=[]
            def dispatch(self,specs):self.ready.extend(self.batch(specs))
            def collect(self,timeout=.2):
                rows=self.ready;self.ready=[];return rows
            def load_archive(self,paths):pass
            def batch(self,specs):
                outputs=[]
                for spec in specs:
                    if self.serial>=self.config.evaluations:break
                    requests.append(spec['kind']);self.serial+=1
                    d=dead if spec['kind']=='baseline' else survived
                    outputs.append((spec,d,str(self.serial)))
                return outputs
        with tempfile.TemporaryDirectory() as d,patch('spire_exact.planning.search.Evaluator',FakeEvaluator):
            pool=SimpleNamespace(stats={},resources=SimpleNamespace(as_dict=lambda:{}))
            result=solve(CTX,Path(d),pool,SearchConfig(evaluations=3))
        self.assertEqual(requests,['baseline','combat_probe','probe_followup'])
        self.assertEqual(result['lower_bound'],0);self.assertEqual(result['upper_bound'],1)
