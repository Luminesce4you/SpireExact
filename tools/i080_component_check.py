"""One bounded DEV component pass, never a campaign efficacy benchmark."""
import argparse, copy, ctypes, hashlib, json, os, subprocess, sys, traceback
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from spire_exact.native import game_data,build_host
from spire_exact.mode1 import context,NativeCampaignBackend
from spire_exact.planning.io import read_json,write_json
from spire_exact.planning.pool import NativePool,read_memory_heartbeat
from spire_exact.planning.resources import ResourcePlan,MIB
from spire_exact.planning.preparation import NativeMap,forge_menu_key


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--game-dir',type=Path,required=True)
    parser.add_argument('--smith-source',type=Path)
    parser.add_argument('--detach',action='store_true')
    args=parser.parse_args();out=args.out.resolve()
    if out.exists():raise ValueError('component output must be new')
    if args.detach:
        from tools.run_seed import spawn_detached
        out.parent.mkdir(parents=True,exist_ok=True)
        command=[sys.executable,Path(__file__).resolve(),*[v for v in sys.argv[1:]if v!='--detach']]
        pid=spawn_detached(command,out.with_suffix('.launcher.log'),ROOT)
        print(json.dumps({'detached_pid':pid,'report':str(out/'report.json')}));return 0
    out.mkdir(parents=True)
    if os.name=='nt':
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.GetCurrentProcess.argtypes=[];kernel.GetCurrentProcess.restype=ctypes.c_void_p
        kernel.SetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
        kernel.SetProcessAffinityMask.restype=ctypes.c_int
        if not kernel.SetProcessAffinityMask(kernel.GetCurrentProcess(),sum(1<<i for i in range(0,16,2))):
            raise ctypes.WinError(ctypes.get_last_error())
    report={'schema':'spire-i080-components/v1','seed':'564940356','panel':'DEV','checks':{},'passed':False,
            'scope':'single-worker bounded native integration; no campaign efficacy, OOM stress or win assertion',
            'counts_as_planner_win':False,'native_request_caps_seconds':[45,90,120,90]}
    def check(name,ok):
        report['checks'][name]=bool(ok);write_json(out/'report.json',report)
        if not ok:raise AssertionError(name)
    try:
        data=game_data(args.game_dir);exe,assembly,stamp=build_host(data)
        report.update(host_sha256=stamp['host_sha256'],dependencies=stamp['inputs']['dependencies'])
        fixture_dir=out/'topology'
        request={'command':'i080_contracts','out':str(fixture_dir/'data')}
        write_json(fixture_dir/'request.json',request)
        env=dict(os.environ,SPIRE_GAME_DATA=str(data),DOTNET_CLI_TELEMETRY_OPTOUT='1')
        done=subprocess.run([exe,str(assembly),str(fixture_dir/'request.json')],env=env,
                            capture_output=True,text=True,timeout=45)
        (fixture_dir/'host.log').write_text(done.stdout+done.stderr,encoding='utf-8')
        check('topology_fixture_process',done.returncode==0)
        fixtures=read_json(fixture_dir/'data/i080-contracts.json')
        check('topology_21_contracts',fixtures.get('passed')is True and len(fixtures.get('cases',[]))==21)
        report['topology_scope']=read_json(fixture_dir/'data/result.json')
        ctx=context('564940356','IRONCLAD',10,'all')
        backend=NativeCampaignBackend(ctx,out/'advisor',data,'combatsolver')
        advisor={**backend.advisor,'budget_ms':120000,'boss_budget_ms':120000,'nodes':5000,
                 'normal_nodes':5000,'beam':45,'profile':'Low','search_mode':'Evaluate',
                 'prefer_f1_hp':True,'quiet_diagnostics':True,'fix_consumed_block_compensation':True}
        resources=ResourcePlan(1,1,1792*MIB,512*MIB,8,16*1024**3)
        with NativePool(data,out/'workers',resources,runtime_profile='server-bounded-large-gen0')as pool:
            baseline=pool.research_progress_baseline(ctx,out/'baseline',45)
            source={k:ctx[k]for k in('seed','character','ascension','unlocks')}
            source.update(history=[],generate_candidate=True,policy_seed=0,max_decisions=30,
                          capture_checkpoints=False,capture_route_graph=True,capture_preparation_menus=True,
                          capture_resource_telemetry=True,memory_telemetry=True,preserve_completed_prefix=True,
                          research_progress=baseline,advisor=advisor,low_io=True,event_driven_settle=True,
                          include_campaign_metadata=True)
            campaign,_=pool.run(source,out/'source',90,fresh=True,disposable=True)
            transport=read_json(out/'source/transport.json')
            heartbeat,issue=read_memory_heartbeat(out/'source/data/memory-latest.json',transport['pid'],
                hashlib.sha256((out/'source/request.json').read_bytes()).hexdigest())
            check('actual_gc_task_end',issue is None and heartbeat['kind']=='task_end')
            report['configured_heap_limit_bytes']=pool.runtime['gc_heap_hard_limit_bytes']
            report['actual_gc_snapshot']=heartbeat
            check('bounded_heap_limit_effective',heartbeat.get('gc_total_available_memory_bytes')==pool.runtime['gc_heap_hard_limit_bytes'])
            runtime=heartbeat.get('native_runtime_configuration')or{}
            check('clr_configuration_recorded',runtime.get('server_gc')is True and runtime.get('is_64_bit_process')is True
                  and isinstance(runtime.get('command_line'),list)and runtime.get('environment',{}).get('DOTNET_GCHeapHardLimit')is not None)
            entries=[s for s in campaign.get('map_decision_sources',[])if s.get('phase')=='map'
                     and isinstance(s.get('source_native_state'),dict)]
            check('real_guarded_map_source',bool(entries))
            entry=entries[0];graph=NativeMap(entry['graph'],entry['available_actions'])
            shops=graph.shops();check('reachable_known_shop',bool(shops))
            target=shops[0];path=graph.path(target)
            plan={'schema':'spire-map-route-plan/v1','source':copy.deepcopy(entry),
                  'moves':[{'act':graph.act,'col':c,'row':r}for c,r in path],
                  'target_shops':[{'act':graph.act,'col':target[0],'row':target[1]}],
                  'shop_policy':'removal-only/v1'}
            consumer=copy.deepcopy(source)
            consumer.update(history=copy.deepcopy(entry['entry_history']),max_decisions=120,map_route_plan=plan)
            route,_=pool.run(consumer,out/'removal-only',120,fresh=True,disposable=True)
            deployed=route.get('map_route_result')or{};report['route_result']=deployed
            purchases=[p for p in route.get('shop_purchase_events',[])if p.get('shop_policy')=='removal-only/v1']
            report['target_shop_purchases']=purchases
            check('real_target_merchant_entered',deployed.get('entry_checked')is True and deployed.get('complete')is True
                  and any(s.get('native_room')=='MerchantRoom'for s in deployed.get('arrived_shops',[])))
            removals=[p for p in purchases if (p.get('item')or{}).get('semantic')=='removal'and p.get('purchase_succeeded')is True]
            check('one_real_target_removal',len(removals)==1 and removals[0]['removal_count_after']==removals[0]['removal_count_before']+1)
            removal=removals[0]
            evidence=route['decision_evidence'];trace=route['trace']
            index=next(i for i in range(removal['index']+1,len(evidence))
                       if evidence[i].get('phase')=='select_cards'and
                       (evidence[i]['observation'].get('selection')or{}).get('purpose')=='Remove')
            cards=evidence[index]['observation']['selection']['cards']
            selected=trace[index]['indices'];removed=cards[selected[0]]['id']if len(selected)==1 else None
            report['removed_card_id']=removed
            check('target_removal_prefers_strike_in_real_starter_deck',removed=='STRIKE_IRONCLAD')
            if args.smith_source:
                saved=read_json(args.smith_source)
                candidates=[i for i,row in enumerate(saved.get('decision_evidence',[]))
                            if row.get('phase')=='rest'and row['observation'].get('act')==2
                            and saved['trace'][i].get('kind')=='rest'
                            and 'SMITH'in saved['trace'][i].get('option','')]
                check('saved_dev_smith_fixture_available',bool(candidates))
                index=candidates[0]
                replay={k:source[k]for k in('seed','character','ascension','unlocks','research_progress',
                    'low_io','event_driven_settle','include_campaign_metadata','memory_telemetry')}
                replay.update(history=copy.deepcopy(saved['trace'][:index+1]),generate_candidate=True,
                              max_decisions=index+1,capture_checkpoints=False,capture_preparation_menus=True)
                result,_=pool.run(replay,out/'smith-prefix',90,fresh=True,disposable=True)
                menus=result.get('preparation_menu_sources')or[]
                report['smith_source']=str(args.smith_source.resolve());report['smith_metadata']=menus
                check('smith_prefix_replayed_exactly',result.get('trace')==replay['history'])
                menu=next((row for row in menus if row.get('index')==index),None)
                check('actual_smith_coord_bound_to_evidence',menu is not None and menu.get('phase')=='rest'
                      and menu.get('observation')==result['decision_evidence'][index]['observation']
                      and forge_menu_key(result['decision_evidence'][index],map_coord=menu.get('map_coord'))is not None)
            else:
                report['smith_prefix_check']='not requested; pure grouping tests only'
        report['passed']=True
    except Exception as error:
        report['error']=str(error);report['traceback']=traceback.format_exc()
    write_json(out/'report.json',report)
    print(json.dumps({'passed':report['passed'],'checks':report['checks'],'report':str(out/'report.json')},ensure_ascii=False))
    return 0 if report['passed']else 1


if __name__=='__main__':raise SystemExit(main())
