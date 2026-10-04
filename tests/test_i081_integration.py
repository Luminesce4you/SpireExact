"""Pure Future/pool integration contracts. No SDK, worker, game or effect data."""
from copy import deepcopy
from concurrent.futures import Future
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock,patch
import json
import unittest

from spire_exact.canonical import canonical
from spire_exact.planning import search
from spire_exact.planning.archive import CheckpointArchive,DiverseFrontier,ResultCache
from spire_exact.planning.final_defaults import FINAL_FEATURES,I081_FEATURES,resolve_entry_defaults
from spire_exact.planning.gatemodel import GateModels
from spire_exact.planning.pool import NativePool,WorkerError
from spire_exact.planning.repairs import RepairQueue
from test_f2_readiness import CTX,INPUTS,baseline,credit,ready,template,trajectory


ADVISOR={'budget_ms':1000,'boss_budget_ms':2000,'nodes':60000,'normal_nodes':10000,
         'binary_identity':{'Fixture.dll':'0'*64},
         'gate_plans':{'Boss':{'members':[{'mode':'Evaluate','beam':45,'nodes':120000}]}}}
IDENTITY=baseline()['native_identity']
OFF={name:False for name in I081_FEATURES}


def raw_probe(request):
    """Strict native protocol shape, with actual fixture prefix and F2 loss."""
    result=trajectory()
    prefix=deepcopy(request['history'])
    before=deepcopy(request['probe']['expected_entry_observation'])
    fight=deepcopy(result['decision_evidence'][-1]['observation'])
    fight.update(floor=50,hp=str(request['probe']['hp']),max_hp=str(request['probe']['hp']),
                 enemies=[{'id':'F2','combat_id':1,'hp':'100','max_hp':'100'}])
    result.update(status='PROBE',synthetic=True,value=None,reason=None,native_terminal_observed=False,
                  consumed=len(prefix),trace=prefix+[{'kind':'probe_enter','encounter':'F2'},{'kind':'end_turn'}],
                  decision_evidence=[deepcopy(result['decision_evidence'][0])for _ in prefix]+[
                      {'phase':'map','observation':before,'available_actions':[]},
                      {'phase':'combat','observation':fight,'available_actions':[{'kind':'end_turn'}]}],
                  observation=dict(before,room='Boss',floor=50,hp='0'),
                  terminal_combat={'act':2,'floor':50,'turn':2,
                      'enemies':[{'id':'F2','combat_id':1,'hp':'40','max_hp':'100'}]},
                  probe={'entered':True,'fought':True,'won':False,'entry_index':len(prefix),'edits':[]},
                  performance={'wall_us':1000,'cpu_us':400,'counters':{}},
                  advisor_metrics={'searches':[{'expanded_nodes':11,'wall_us':200}]})
    progress=request.get('research_progress')
    if progress is not None:
        result['research_progress']={'schema':'spire-research-progress/v1',
            'baseline_sha256':progress['baseline_sha256'],'checkpoint_restored':False}
    return result


def stage_spec():
    sampler=ready()
    sampler.observe(trajectory(),'fixture-source',0,template())
    credit(sampler)
    return sampler.next_stage()


class FuturePool:
    def __init__(self,*,pending_probes=False):
        self.inputs=deepcopy(INPUTS)
        self.calls=[];self.probe_calls=[];self.probe_futures=[];self.validated=[]
        self.stats={}
        self.resources=SimpleNamespace(workers=2,as_dict=lambda:{'fixture':True,'workers':2})
        self.runtime={'profile':'pure-fixture'}
        self.pending_probes=pending_probes

    def research_progress_baseline(self,ctx,out,timeout):
        return baseline()

    def validate_inputs(self,request):
        self.validated.append(deepcopy(request))

    def submit(self,request,out,timeout,**options):
        self.calls.append(deepcopy(request))
        # Keep every supplied replay prefix intact. New restarts get distinct
        # full entries while sharing measured deck/relic readiness only.
        tag=next((action['col']for action in request['history']if action.get('kind')=='map'),
                 request['policy_seed'])
        result=trajectory(tag)
        result.update(consumed=len(request['history']),restored_prefix=0,checkpoints=[],
                      performance={'wall_us':10000000,'cpu_us':1000000,'counters':{}},
                      advisor_metrics={'searches':[{'expanded_nodes':13,'wall_us':1000000}]})
        future=Future();future.set_result((result,deepcopy(IDENTITY)))
        return future

    def submit_probes(self,requests,out,timeout):
        self.probe_calls.append(deepcopy(requests))
        future=Future();self.probe_futures.append(future)
        if not self.pending_probes:
            future.set_result([(raw_probe(request),None)for request in requests])
        return future

    def cancel_pending(self):
        for future in self.probe_futures:
            if not future.done():future.cancel()


def evaluator(directory,*,pending_probes=False,**options):
    pool=FuturePool(pending_probes=pending_probes)
    config=search.SearchConfig(**options)
    ev=search.Evaluator(pool,CTX,Path(directory),config,deepcopy(ADVISOR))
    ev.identity=deepcopy(IDENTITY)
    ev.cache=SimpleNamespace(get=Mock(return_value=None),put=Mock())
    ev.checkpoints=SimpleNamespace(nearest=Mock(return_value=None),import_result=Mock())
    ev.record=ev.records.append
    return ev,pool


class SerialModel(GateModels):
    """An ordinal-sensitive prior, independent of the fixed outcome stream."""
    def tiers(self,serial):
        return {'card:SERIAL':[serial,-serial,serial+1]}


def run_toy(*,sampling):
    # Actual Evaluator.dispatch/collect and actual solve are exercised. Only
    # native execution and wall time are replaced; this is not a solver run.
    config=search.SearchConfig(evaluations=18,seconds=60,task_seconds=5,scheduler='focus',
        dispatch_mode='ordered',dispatch_window=4,solver_seed=23,prior=True,repair_mode='gate',
        gate_retry_plans=({'gate_plans':{'Boss':{'members':[{'mode':'Evaluate','beam':68,'nodes':240000}]}}},
                          {'gate_plans':{'Boss':{'members':[{'mode':'Evaluate','beam':135,'nodes':480000}]}}}),
        lookahead_floors=99,lookahead_actions=12000,max_decisions=12000,
        f2_readiness_probes=sampling,f2_readiness_every=1)
    pool=FuturePool()
    observed={'model':[],'frontier':[],'archive':[],'cache':[]}
    original_model=GateModels.add
    original_frontier=DiverseFrontier.add
    original_archive=CheckpointArchive.import_result
    original_cache=ResultCache.put

    def model_add(owner,result,*args,**kwargs):
        observed['model'].append(bool(result.get('synthetic')))
        return original_model(owner,result,*args,**kwargs)

    def frontier_add(owner,result,*args,**kwargs):
        observed['frontier'].append(bool(result.get('synthetic')))
        return original_frontier(owner,result,*args,**kwargs)

    def archive_add(owner,result,*args,**kwargs):
        observed['archive'].append(bool(result.get('synthetic')))
        return original_archive(owner,result,*args,**kwargs)

    def cache_put(owner,key,value,*args,**kwargs):
        observed['cache'].append(bool(value[0].get('synthetic')))
        return original_cache(owner,key,value,*args,**kwargs)

    with TemporaryDirectory()as directory,patch.object(search,'perf_counter',return_value=0.0),\
         patch.object(search,'runtime_snapshot',return_value={}),patch('spire_exact.planning.gatemodel.GateModels',SerialModel),\
         patch.object(GateModels,'add',model_add),patch.object(DiverseFrontier,'add',frontier_add),\
         patch.object(CheckpointArchive,'import_result',archive_add),patch.object(ResultCache,'put',cache_put):
        result=search.solve(CTX,Path(directory),pool,config,advisor=deepcopy(ADVISOR))
        json.dumps(result)
    return result,pool,observed


class I081IntegrationTests(unittest.TestCase):
    def test_default_profiles_dependencies_and_carry_conflict(self):
        for profile,enabled in (('i081',True),('i075-final',False),('legacy',False)):
            args=SimpleNamespace(feature_profile=profile,ascension=None,nodes=None,
                                 **{key:None for key in FINAL_FEATURES|I081_FEATURES})
            resolved=resolve_entry_defaults(args)
            self.assertEqual([getattr(resolved,name)for name in I081_FEATURES],[enabled]*4)
        self.assertEqual([getattr(search.SearchConfig(),name)for name in I081_FEATURES],[False]*4)
        self.assertEqual([getattr(search.SearchConfig.final(),name)for name in I081_FEATURES],[True]*4)
        for name in ('f2_dead_retry','f2_joint_focus','f2_joint_model'):
            with self.subTest(name=name),self.assertRaises(ValueError):
                search.SearchConfig(**{name:True})
        with self.assertRaises(ValueError):
            search.SearchConfig(f2_readiness_probes=True,f2_joint_focus=True,focus_carry=True)

    def test_four_off_native_request_matches_independent_i080_bytes(self):
        # Independent literal from i080's request contract; not two calls to
        # the modified request builder. Check every transmitted field.
        prefix=[{'kind':'map','col':1,'row':15}]
        expected={'seed':'42','character':'IRONCLAD','ascension':10,'unlocks':'all',
            'history':prefix,'generate_candidate':True,'policy_seed':9,'max_decisions':777,
            'capture_checkpoints':True,'low_io':True,'event_driven_settle':True,
            'include_campaign_metadata':True,'stop_at_floor':51,
            'advisor':{**deepcopy(ADVISOR),'budget_ms':2000,'boss_budget_ms':9999,'nodes':120000,
                       'fix_consumed_block_compensation':True,'quiet_diagnostics':True}}
        with TemporaryDirectory()as directory:
            ev,pool=evaluator(directory,low_io=True,event_driven_settle=True,**OFF)
            request=ev.request(deepcopy(prefix),policy=9,horizon=51,actions=777,
                               advisor_scale=2,boss_budget_ms=9999)
            self.assertEqual(canonical(request),canonical(expected))
            ev.dispatch([{'kind':'macro_focus','request':request}])
            self.assertEqual(canonical(pool.calls[0]),canonical(expected))
            self.assertEqual(pool.probe_calls,[])

    def test_only_sampling_does_not_consume_real_budget_prior_serial_or_window(self):
        with TemporaryDirectory()as plain_dir,TemporaryDirectory()as sample_dir:
            plain,pool_plain=evaluator(plain_dir,evaluations=2)
            sample,pool_sample=evaluator(sample_dir,evaluations=2,f2_readiness_probes=True)
            for ev in (plain,sample):
                ev.prior_provider=lambda serial:{'card:SERIAL':[serial,-serial]}
                ev.dispatch([{'kind':'restart','request':ev.request([],policy=1)}])
            sample.dispatch([stage_spec()])
            self.assertEqual((sample.serial,sample.f2_probe_evaluations,sample.allocation_serial),(2,1,1))
            self.assertEqual(sample.allocation_pending_count,plain.pending_count)
            for ev in (plain,sample):
                ev.dispatch([{'kind':'restart','request':ev.request([],policy=2)}])
            self.assertEqual([canonical(request)for request in pool_sample.calls],
                             [canonical(request)for request in pool_plain.calls])
            self.assertEqual(len(pool_sample.calls),2)
            self.assertEqual(sample.allocation_serial,2)
            self.assertEqual(len(pool_sample.probe_calls),1)

    def test_pending_sample_does_not_block_ordered_real_completion(self):
        with TemporaryDirectory()as directory,patch.object(search,'runtime_snapshot',return_value={}):
            ev,pool=evaluator(directory,pending_probes=True,f2_readiness_probes=True)
            ev.dispatch([stage_spec()])
            ev.dispatch([{'kind':'restart','request':ev.request([],policy=3)}])
            rows=ev.collect(timeout=0,ordered=True)
            self.assertEqual([spec['kind']for spec,_,_ in rows],['restart'])
            self.assertFalse(pool.probe_futures[0].done())
            self.assertEqual(ev.allocation_pending_count,0)
            self.assertEqual(ev.pending_count,1)
            pool.probe_futures[0].set_result([(raw_probe(request),None)for request in pool.probe_calls[0]])
            self.assertEqual(ev.collect(timeout=0,ordered=True)[0][0]['kind'],'f2_readiness_probe')

    def test_strict_collector_rejects_forged_win_and_never_uses_real_cache_or_archive(self):
        with TemporaryDirectory()as directory,patch.object(search,'runtime_snapshot',return_value={}):
            ev,pool=evaluator(directory,pending_probes=True,f2_readiness_probes=True)
            spec=stage_spec();ev.dispatch([spec])
            raw=[(raw_probe(request),None)for request in spec['requests']]
            forged=deepcopy(raw[0][0]);forged.update(status='TERMINAL',value=[1],native_terminal_observed=True)
            raw[0]=(forged,None);pool.probe_futures[0].set_result(raw)
            batch=ev.collect(timeout=0,ordered=True)[0][1]
            self.assertTrue(batch['synthetic']);self.assertEqual(batch['trace'],[])
            self.assertIsNone(batch['outcomes'][0]);self.assertTrue(all(row is not None for row in batch['outcomes'][1:]))
            self.assertEqual(ev.records[0]['classification'],'SYNTHETIC_PROBE')
            self.assertEqual(pool.calls,[])
            self.assertFalse(ev.cache.get.called);self.assertFalse(ev.cache.put.called)
            self.assertFalse(ev.checkpoints.nearest.called);self.assertFalse(ev.checkpoints.import_result.called)

    def test_shared_pool_rejects_probe_before_any_worker_or_sdk_access(self):
        pool=NativePool.__new__(NativePool)
        with TemporaryDirectory()as directory,self.assertRaisesRegex(WorkerError,'PROBE_REQUIRES_DISPOSABLE_WORKER'):
            pool.run(stage_spec()['requests'][0],Path(directory)/'unused',fresh=False,disposable=False)

    def test_actual_solve_sampling_keeps_real_stream_and_repair_share_and_isolates_synthetic(self):
        plain,pool_plain,plain_calls=run_toy(sampling=False)
        sampled,pool_sample,sample_calls=run_toy(sampling=True)
        self.assertEqual([canonical(request)for request in pool_sample.calls],
                         [canonical(request)for request in pool_plain.calls])
        self.assertEqual(len(pool_plain.calls),18)
        self.assertGreater(len(pool_sample.probe_calls),0)  # Must actually exercise the added branch.
        self.assertEqual(sampled['search_metrics']['scheduled_by_kind'].get('gate_retry',0),
                         plain['search_metrics']['scheduled_by_kind'].get('gate_retry',0))
        self.assertGreater(plain['search_metrics']['scheduled_by_kind'].get('gate_retry',0),0)
        self.assertGreater(sampled['search_metrics']['f2_readiness']['sampling_evaluations'],0)
        for kind,observed in sample_calls.items():
            self.assertTrue(observed,kind)  # A no-op fake cannot pass this isolation check.
            self.assertFalse(any(observed),kind)
        self.assertEqual(len(sample_calls['model']),len(plain_calls['model']))

    def test_repair_blocked_then_deferred_and_only_dead_fallback_preserve_priority(self):
        queue=RepairQueue('gate')
        blocked={'id':'blocked','priority':(99,),'dead':False,'blocked':True}
        first={'id':'dead-high','priority':(9,),'dead':True,'blocked':False}
        other={'id':'ordinary','priority':(1,),'dead':False,'blocked':False}
        last={'id':'dead-low','priority':(2,),'dead':True,'blocked':False}
        for item in (blocked,first,other,last):queue.append(item)
        eligible=lambda item:item['blocked']
        deferred=lambda item:item['dead']
        self.assertIs(queue.popleft(blocked=eligible,deferred=deferred),other)
        self.assertEqual(queue.items,[blocked,first,last])
        self.assertIs(queue.popleft(blocked=eligible,deferred=deferred),first)
        self.assertIs(queue.popleft(blocked=eligible,deferred=deferred),last)
        self.assertIsNone(queue.popleft(blocked=eligible,deferred=deferred))
        self.assertEqual(queue.items,[blocked])


if __name__=='__main__':
    unittest.main()
