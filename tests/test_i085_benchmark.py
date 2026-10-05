"""No-DLL audit fixtures. Passing these does NOT create native win evidence."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from spire_exact.mode1 import context, check_winning_replay
from spire_exact.planning.io import write_json, read_json
from tools.benchmark_i085 import (create_plan, recorded_win_audit, work_summary,
    inspect_run, compare_rows, report, execute, source_hash)
from tools.run_release_source import detached_arguments
from test_mode1 import winning
from test_research_evaluator import fixture, native_result, IDENTITY


def write_audit_fixture(folder):
    """Only fabricated contract-consistency data; no game host has been run."""
    ctx=context('101','IRONCLAD',10,'all')
    native={'game_sha256':'a'*64,'host_sha256':'b'*64}
    data=winning();label='eval-0001-baseline';verify=folder/('verify-'+label)
    identity={'context':ctx,'native':native}
    certificate=check_winning_replay(data,deepcopy(data),identity,identity)
    request={k:ctx[k] for k in ('seed','character','ascension','unlocks')}
    request.update(command='replay',generate_candidate=False,history=data['trace'],expected_evidence=data['decision_evidence'])
    result={'status':'VERIFIED_WIN_IN_NATIVE_HOST','stop_reason':'verified_boolean_maximum',
            'context':ctx,'elapsed_seconds':25,'configuration':{'solver_seed':271828},
            'resources':{'workers':7,'dop':1},'evaluations':[{'label':label,'kind':'baseline',
            'completed_wall_seconds':20,'classification':'NATIVE_WIN_CANDIDATE','observation':{'act':2}}]}
    for path,value in ((folder/'certificate.json',certificate),(folder/'winning-route.json',{'context':ctx,'trace':data['trace']}),
        (verify/'request.json',request),(folder/label/'data/decision.json',data),(folder/label/'data/identity.json',native),
        (verify/'data/decision.json',data),(verify/'data/identity.json',native),(folder/'result.json',result),
        (folder/'verification-timing.json',{'candidate_label':label,'verified_wall_seconds':24})):
        write_json(path,value)
    return result,verify


def row(arm,win=False,t=None,seed='101',solver_seed=1):
    return {'id':f'{seed}-{solver_seed}-{arm}','seed':seed,'solver_seed':solver_seed,'arm':arm,
        'available':True,'run_state':'completed','status':'VERIFIED_WIN_IN_NATIVE_HOST' if win else 'UNKNOWN',
        'context_matches_plan':True,'settings_match_plan':True,'native_identities':[{'host':'x'}],
        'wall_cap_seconds':60,'verified_evidence':win,'within_coordinator_budget_win':
            (t<=60 if t is not None else None) if win else False,'time_to_first_verified_win_seconds':t}


class BenchmarkPlanningTests(unittest.TestCase):
    def test_rotating_order_and_settings(self):
        with tempfile.TemporaryDirectory() as d:
            plan=create_plan(Path(d)/'new',['101','102'],[3,4],['i082','i085','i085-routes'],1,7)
            self.assertEqual(len(plan['runs']),12)
            self.assertEqual([r['arm'] for r in plan['runs'][:6]],['i082','i085','i085-routes','i085','i085-routes','i082'])
            self.assertFalse(plan['native_executed_during_plan'])
            for r in plan['runs']:
                self.assertEqual(r['effective_parameters']['nodes'],60000)
                self.assertEqual(r['effective_parameters']['normal_nodes'],10000)
                self.assertEqual(r['effective_parameters']['dispatch_window'],56)
                self.assertFalse(Path(r['out']).exists())
            with self.assertRaises(ValueError):create_plan(Path(d)/'new',['101'],[3],['i082'])

    def test_invalid_panels_never_create_output(self):
        with tempfile.TemporaryDirectory() as d:
            for seeds,solvers,arms in (([],[1],['i082']),(['x','x'],[1],['i082']),(['101'],[True],['i082']),(['101'],[1],['bad'])):
                with self.subTest(seeds=seeds,solvers=solvers,arms=arms),self.assertRaises(ValueError):
                    create_plan(Path(d)/'new',seeds,solvers,arms)
            self.assertFalse((Path(d)/'new').exists())

    def test_empty_report_does_not_invent_zero_native_cost_or_losses(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'new';create_plan(out,['101'],[1],['i082','i085'],1)
            result=report(out)
            self.assertTrue(all(r['run_state']=='not_run' for r in result['runs']))
            self.assertEqual(result['comparisons'][0]['auditable_pairs'],0)
            self.assertIn('| unknown | unknown |',(out/'comparison.md').read_text())

    def test_non_windows_run_fails_without_starting_native(self):
        with patch('tools.benchmark_i085.os.name','posix'),self.assertRaises(ValueError):
            execute(Path('/does-not-exist'))

    def test_detach_flag_stays_before_extra(self):
        args=['--seed','101','--detach','--extra','--macro-routes','on']
        self.assertEqual(detached_arguments(args),['--seed','101','--detached-child','--extra','--macro-routes','on'])
        self.assertEqual(detached_arguments(['--detach','--seed','101']),['--seed','101','--detached-child'])


class BenchmarkEvidenceTests(unittest.TestCase):
    def test_contract_artifacts_accepted_without_claiming_execution(self):
        with tempfile.TemporaryDirectory() as d:
            result,_=write_audit_fixture(Path(d))
            audited=recorded_win_audit(Path(d),result)
            self.assertTrue(audited['verified_evidence'])
            self.assertFalse(audited['new_native_replay_executed_by_report'])

    def test_certificate_without_replay_is_insufficient(self):
        with tempfile.TemporaryDirectory() as d:
            result,verify=write_audit_fixture(Path(d));(verify/'data/decision.json').unlink()
            self.assertFalse(recorded_win_audit(Path(d),result)['verified_evidence'])

    def test_changed_route_identity_or_evidence_rejected(self):
        for target,field,value in (('route','trace',[]),('identity','host_sha256','c'*64),('replay','decision_evidence',[])):
            with self.subTest(target=target),tempfile.TemporaryDirectory() as d:
                folder=Path(d);result,verify=write_audit_fixture(folder)
                path=folder/'winning-route.json' if target=='route' else verify/'data'/('identity.json' if target=='identity' else 'decision.json')
                data=read_json(path);data[field]=value;write_json(path,data)
                self.assertFalse(recorded_win_audit(folder,result)['verified_evidence'])

    def test_advisor_checkpoint_synthetic_consumer_requests_rejected(self):
        for field in ('advisor','checkpoint','probe','card_menu_probe','map_route_plan','f1_winner_proposal'):
            with self.subTest(field=field),tempfile.TemporaryDirectory() as d:
                folder=Path(d);result,verify=write_audit_fixture(folder)
                request=read_json(verify/'request.json');request[field]={};write_json(verify/'request.json',request)
                self.assertFalse(recorded_win_audit(folder,result)['verified_evidence'])

    def test_recorded_synthetic_status_cannot_count_win(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);result,_=write_audit_fixture(folder);result['status']='PROBE_BATCH'
            self.assertFalse(recorded_win_audit(folder,result)['verified_evidence'])

    def test_missing_costs_unknown_cache_not_double_billed(self):
        records=[{'kind':'macro_focus','performance':{'wall_us':1500000},'expanded_combat_nodes':17},
                 {'kind':'macro_focus'}, {'kind':'macro_focus','cache_hit':True,'performance':{'wall_us':999999999}},
                 {'kind':'paired_card_probe','classification':'SYNTHETIC_PROBE','probe_native_seconds':None}]
        summary=work_summary(records)
        self.assertEqual(summary['macro_focus']['native_seconds_measured'],1.5)
        self.assertEqual(summary['macro_focus']['native_seconds_unknown'],1)
        self.assertEqual(summary['macro_focus']['combat_nodes_unknown'],1)
        self.assertEqual(summary['paired_card_probe']['native_seconds_unknown'],1)

    def test_partial_probe_cost_keeps_known_subset_and_unknown_count(self):
        result=work_summary([{'kind':'paired_card_probe','classification':'SYNTHETIC_PROBE',
            'probe_native_seconds':None,'measured_probe_native_seconds':2.5,
            'expanded_combat_nodes':None,'measured_expanded_combat_nodes':100},
            {'kind':'macro_focus','expanded_combat_nodes':0,
             'research_work':{'metrics':{'expanded_combat_nodes':None}}}])
        self.assertEqual(result['paired_card_probe']['native_seconds_measured'],2.5)
        self.assertEqual(result['paired_card_probe']['native_seconds_unknown'],1)
        self.assertEqual(result['paired_card_probe']['combat_nodes_measured'],100)
        self.assertEqual(result['paired_card_probe']['combat_nodes_unknown'],1)
        self.assertEqual(result['macro_focus']['combat_nodes_unknown'],1)

    def test_inspection_does_not_use_candidate_timestamp_as_verified(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)/'run';result,_=write_audit_fixture(folder)
            (folder/'verification-timing.json').unlink()
            spec={'id':'x','seed':'101','solver_seed':271828,'arm':'i085','wall_cap_seconds':60,'out':str(folder),'effective_parameters':{}}
            inspected=inspect_run(spec)
            self.assertTrue(inspected['verified_evidence'])
            self.assertIsNone(inspected['time_to_first_verified_win_seconds'])
            self.assertIsNone(inspected['within_coordinator_budget_win'])
            self.assertEqual(inspected['candidate_seconds'],20)
            self.assertFalse(inspected['settings_match_plan'])

    def test_manifest_resource_and_timing_validation(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)/'run';result,_=write_audit_fixture(folder)
            wanted={'workers':7,'dop':1,'macro_routes':'shadow','solver_seed':271828}
            spec={'id':'x','seed':'101','solver_seed':271828,'arm':'i085','wall_cap_seconds':60,'out':str(folder),'effective_parameters':wanted}
            manifest={'seed':'101','solver_seed':271828,'wall_cap_seconds':60,'fresh_start':True,'effective_parameters':wanted}
            write_json(folder.parent/'run.validation-manifest.json',manifest)
            spec['source_hash']='fixturehash'
            write_json(folder.parent/'run-resources.json',{'enforced':True,'wall_limit_seconds':60,
                'workload_limit_bytes':14336*1024**2,'logical_cpu_count':8,'hard_timeout':False,'storage_limit':False})
            write_json(folder.parent/'run.benchmark-status.json',{'state':'finished','returncode':0,
                'source_hash_before':'fixturehash','source_hash_after':'fixturehash'})
            inspected=inspect_run(spec);self.assertTrue(inspected['settings_match_plan']);self.assertTrue(inspected['within_coordinator_budget_win'])
            write_json(folder/'verification-timing.json',{'candidate_label':'eval-0001-baseline','verified_wall_seconds':19})
            inspected=inspect_run(spec);self.assertIsNone(inspected['time_to_first_verified_win_seconds'])
            self.assertIn('verification_timestamp_outside_candidate_final_interval',inspected['notes'])
            result['resources']['workers']=6;write_json(folder/'result.json',result)
            self.assertFalse(inspect_run(spec)['settings_match_plan'])


class BenchmarkComparisonTests(unittest.TestCase):
    def test_discordant_wins_and_restricted_time(self):
        a=row('i082');b=row('i085',True,15)
        c=compare_rows([a,b],['i082','i085'])[0]
        self.assertEqual(c['candidate_only_wins'],1)
        self.assertEqual(c['paired_mean_restricted_time_delta_seconds'],-45)

    def test_invalid_verified_claim_not_relabelled_as_an_unsolved_seed(self):
        a=row('i082');b=row('i085');b['status']='VERIFIED_WIN_IN_NATIVE_HOST'
        c=compare_rows([a,b],['i082','i085'])[0]
        self.assertEqual(c['auditable_pairs'],0)
        self.assertEqual(c['omitted_pairs'][0]['reason'],'verification_artifact_audit_failed')

    def test_unknown_win_time_or_incomplete_run_excluded(self):
        for b in (row('i085',True,None),{**row('i085'),'run_state':'partial'}):
            c=compare_rows([row('i082'),b],['i082','i085'])[0]
            self.assertEqual(c['auditable_pairs'],0)

    def test_after_budget_verified_win_recorded_but_not_budget_success(self):
        c=compare_rows([row('i082'),row('i085',True,65)],['i082','i085'])[0]
        self.assertEqual(c['neither_win'],1)
        self.assertEqual(c['paired_mean_restricted_time_delta_seconds'],0)

    def test_mixed_binary_or_changed_settings_excluded(self):
        for changes in ({'native_identities':[{'host':'y'}]},{'settings_match_plan':False},{'context_matches_plan':False}):
            c=compare_rows([row('i082'),{**row('i085'),**changes}],['i082','i085'])[0]
            self.assertEqual(c['auditable_pairs'],0)

    def test_different_cpu_partition_not_a_fair_pair(self):
        a={**row('i082'),'job_resources':{'cpu_affinity':list(range(8))}}
        b={**row('i085'),'job_resources':{'cpu_affinity':list(range(8,16))}}
        compared=compare_rows([a,b],['i082','i085'])[0]
        self.assertEqual(compared['auditable_pairs'],0)
        self.assertEqual(compared['omitted_pairs'][0]['reason'],'resource_partition_mismatch')

    def test_cluster_interval_counts_game_seeds_not_solver_repeats(self):
        runs=[]
        for seed in ('101','102'):
            for solver in (1,2,3):runs.extend([row('i082',seed=seed,solver_seed=solver),row('i085',True,30,seed,solver)])
        c=compare_rows(runs,['i082','i085'])[0]
        self.assertEqual(c['seed_clusters_for_interval'],2)
        self.assertEqual(c['seed_cluster_bootstrap_interval'],[-30,-30])


class MacroEvaluatorRegressionTests(unittest.TestCase):
    def test_i085_cache_hit_remains_zero_work_and_keeps_macro_origin(self):
        with tempfile.TemporaryDirectory() as d:
            ev,pool=fixture(d,scheduler='focus',macro_plateau=True,macro_widening=True,macro_fair=True)
            ev.cache.get.return_value=(native_result(),IDENTITY)
            origin={'source':'p','act':0,'index':3,'category':'focus'}
            ev.dispatch([{'kind':'macro_focus','group':origin,'request':ev.request([])}]);ev.collect()
            self.assertEqual(pool.calls,[]);self.assertEqual(ev.checkpoints.nearest.call_count,0)
            self.assertTrue(ev.records[0]['cache_hit']);self.assertEqual(ev.records[0]['macro_origin'],origin)

    def test_route_consumer_preserves_source_policy_and_is_isolated(self):
        with tempfile.TemporaryDirectory() as d,patch('spire_exact.planning.search.runtime_snapshot',return_value={}):
            ev,pool=fixture(d,scheduler='focus',macro_routes='on')
            from unittest.mock import Mock
            ev.prior_provider=Mock(return_value={'card:NEW':[1,1,1]})
            request=ev.request([]);request.update(policy_seed=177,policy_prior={'card:OLD':[1,0,-1]},map_route_plan={'fixture':True})
            ev.dispatch([{'kind':'macro_route_portfolio','request':request}]);ev.collect()
            sent,opts=pool.calls[0]
            self.assertEqual(opts,{'fresh':True,'disposable':True})
            self.assertEqual(sent['policy_seed'],177);self.assertEqual(sent['policy_prior'],{'card:OLD':[1,0,-1]})
            self.assertFalse(ev.prior_provider.called);self.assertFalse(ev.cache.get.called)
            self.assertFalse(ev.cache.put.called);self.assertFalse(ev.checkpoints.import_result.called)


if __name__=='__main__':unittest.main()
