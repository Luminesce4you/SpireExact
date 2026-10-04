"""Bounded i075 native components; never a campaign benchmark or win claim."""
import argparse, copy, ctypes, hashlib, json, os, subprocess, sys, traceback
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from spire_exact.native import game_data, build_host
from spire_exact.mode1 import context, NativeCampaignBackend
from spire_exact.planning.io import read_json, write_json
from spire_exact.planning.pool import NativePool, read_memory_heartbeat
from spire_exact.planning.resources import ResourcePlan, MIB
from spire_exact.planning.memory_prefix import recover_completed_prefix
from spire_exact.planning.preparation import NativeMap


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--game-dir',type=Path,required=True)
    args=parser.parse_args();out=args.out.resolve()
    if out.exists():raise ValueError('component output must be new')
    out.mkdir(parents=True)
    if os.name=='nt':
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.GetCurrentProcess.restype=ctypes.c_void_p
        kernel.SetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
        if not kernel.SetProcessAffinityMask(kernel.GetCurrentProcess(),sum(1<<i for i in range(0,16,2))):
            raise ctypes.WinError(ctypes.get_last_error())
    report={'schema':'spire-i075-component/v1','scope':'bounded native components, not an end-to-end campaign or performance comparison',
            'seed':'564940356','checks':{},'passed':False,'counts_as_planner_win':False}
    def check(name,ok):
        report['checks'][name]=bool(ok)
        write_json(out/'report.json',report)
        if not ok:raise AssertionError(name)
    try:
        data=game_data(args.game_dir);exe,assembly,stamp=build_host(data)
        report['host_sha256']=stamp['host_sha256'];report['dependencies']=stamp['inputs']['dependencies']
        # Pure native selection/DTO fixtures, not solver or live combat evidence.
        request={'command':'f1_quality_contracts','compact':True,'out':str(out/'f1-fixtures/data')}
        write_json(out/'f1-fixtures/request.json',request)
        env=dict(os.environ,SPIRE_GAME_DATA=str(data),DOTNET_CLI_TELEMETRY_OPTOUT='1')
        result=subprocess.run([exe,str(assembly),str(out/'f1-fixtures/request.json')],env=env,capture_output=True,text=True,timeout=45)
        (out/'f1-fixtures/host.log').write_text(result.stdout+result.stderr,encoding='utf-8')
        check('f1_fixture_process',result.returncode==0)
        fixtures=read_json(out/'f1-fixtures/data/f1-quality-contracts.json')
        check('f1_fixture_cases',fixtures.get('passed') is True and len(fixtures.get('cases',[]))==20)
        ctx=context('564940356','IRONCLAD',10,'all')
        backend=NativeCampaignBackend(ctx,out/'advisor',data,'combatsolver')
        advisor={**backend.advisor,'budget_ms':120000,'boss_budget_ms':120000,'nodes':5000,'normal_nodes':5000,
                 'beam':45,'profile':'Low','search_mode':'Evaluate','prefer_f1_hp':True,'quiet_diagnostics':True,
                 'fix_consumed_block_compensation':True}
        resources=ResourcePlan(1,1,1792*MIB,512*MIB,8,16*1024**3)
        with NativePool(data,out/'workers',resources,runtime_profile='server-bounded-large-gen0') as pool:
            baseline=pool.research_progress_baseline(ctx,out/'baseline',45)
            source={k:ctx[k]for k in('seed','character','ascension','unlocks')}
            source.update(history=[],generate_candidate=True,policy_seed=0,max_decisions=30,capture_checkpoints=False,
                          capture_route_graph=True,capture_resource_telemetry=True,memory_telemetry=True,
                          preserve_completed_prefix=True,research_progress=baseline,advisor=advisor,
                          low_io=True,event_driven_settle=True,include_campaign_metadata=True)
            campaign,identity=pool.run(source,out/'source',90,fresh=True,disposable=True)
            # Exact post-transport request includes command/out/compact. Bind to it.
            actual=read_json(out/'source/request.json')
            latest,issue=read_memory_heartbeat(out/'source/data/memory-latest.json',
                read_json(out/'source/transport.json')['pid'],hashlib.sha256((out/'source/request.json').read_bytes()).hexdigest())
            check('real_gc_task_end_heartbeat',issue is None and latest['kind']=='task_end')
            check('completed_action_counter',latest['progress']['completed_prefix_length']==len(campaign.get('trace',[])))
            report['configured_heap_limit_bytes']=pool.runtime['gc_heap_hard_limit_bytes']
            report['native_gc_snapshot']=latest
            recovered=recover_completed_prefix(out/'source',actual,stamp,'NATIVE_TASK_MEMORY_BUDGET')
            check('native_safe_journal_simulated_abort',recovered is not None and recovered[0]['status']=='UNKNOWN' and recovered[0]['value'] is None)
            # No real memory kill is claimed: exercise coordinator validation of
            # a real completed journal under an injected resource-loss reason.
            valid=[s for s in campaign.get('map_decision_sources',[])if s.get('phase')=='map' and isinstance(s.get('source_native_state'),dict)]
            check('actual_map_source',bool(valid))
            entry=valid[0];graph=NativeMap(entry['graph'],entry['available_actions'])
            check('special_boss_node_exported',graph.boss in graph.nodes)
            shops=graph.shops();check('reachable_known_shop',bool(shops))
            path=graph.path(shops[0])
            plan={'schema':'spire-map-route-plan/v1','source':copy.deepcopy(entry),
                  'moves':[{'act':graph.act,'col':c,'row':r}for c,r in path],
                  'target_shops':[{'act':graph.act,'col':shops[0][0],'row':shops[0][1]}],
                  'shop_policy':'potions_remove_relic/v1'}
            consumer=copy.deepcopy(source);consumer.update(history=copy.deepcopy(entry['entry_history']),
                max_decisions=120,map_route_plan=plan,capture_checkpoints=False)
            route,_=pool.run(consumer,out/'route',120,fresh=True,disposable=True)
            deployed=route.get('map_route_result')or{}
            report['route_result']=deployed;report['route_reason']=route.get('reason')
            check('source_entry_guard_passed',deployed.get('entry_checked') is True)
            check('route_deployed_to_real_merchant',deployed.get('complete') is True and any(
                s.get('native_room')=='MerchantRoom'for s in deployed.get('arrived_shops',[])))
            check('native_shop_inventory',bool(route.get('shop_inventory_sources')))
            purchases=route.get('shop_purchase_events')or[]
            check('native_shop_purchase_bool',bool(purchases)and all(type(p.get('purchase_succeeded'))is bool for p in purchases))
            check('target_shop_preparation_active',deployed.get('shop_policy')=='potions_remove_relic/v1' and any(
                p.get('shop_policy')=='potions_remove_relic/v1' and p.get('purchase_succeeded') is True for p in purchases))
            report['shop_purchases']=purchases
            # Same request/history, corrupt only expected observation. No state
            # mutation; strict guard must reject rather than silently fall back.
            bad=copy.deepcopy(consumer);bad['map_route_plan']['source']['entry_observation']['hp']='999999'
            rejected,_=pool.run(bad,out/'guard-rejection',60,fresh=True,disposable=True)
            failed=rejected.get('map_route_result')or{}
            check('bad_expected_state_rejected',bool(rejected.get('reason'))and failed.get('entry_checked') is False
                  and failed.get('complete') is False and rejected.get('value') is None)
        report['passed']=True
    except Exception as error:
        report['error']=str(error);report['traceback']=traceback.format_exc()
    write_json(out/'report.json',report)
    print(json.dumps({'passed':report['passed'],'checks':report['checks'],'report':str(out/'report.json')},ensure_ascii=False))
    return 0 if report['passed']else 1


if __name__=='__main__':raise SystemExit(main())
