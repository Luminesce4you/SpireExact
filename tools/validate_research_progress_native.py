"""Finite native Progress/CP/F1 deployment contracts using an existing prefix.

No seed search, synthetic rollout, promotion, or pruning. This script is run
only after the pure suite/build and writes every native attempt separately.
"""
import argparse,copy,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.canonical import canonical
from spire_exact.mode1 import context
from spire_exact.native import game_data
from spire_exact.planning.io import read_json,write_json
from spire_exact.planning.pool import NativePool
from spire_exact.planning.resources import ResourcePlan
from spire_exact.planning.f1_winners import F1WinnerReuse


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--game-dir',type=Path)
    args=parser.parse_args();out=args.out.resolve()
    if out.exists()and any(out.iterdir()):parser.error('use a new output directory')
    out.mkdir(parents=True,exist_ok=True)
    original=read_json(args.source)
    ctx=context(original['seed'],original['character'],original['ascension'],original['unlocks'])
    resources=ResourcePlan.detect(1,1,3072,2048)
    report={'scope':'finite actual DLL commands in offline TestMode; no Godot parity or speed claim',
        'source_request':str(args.source.resolve()),'resources':resources.as_dict(),
        'checks':[],'passed':False,'native_requests':[]}
    def check(name,passed,**detail):
        report['checks'].append({'name':name,'passed':bool(passed),**detail})
        write_json(out/'report.json',report);print(name,bool(passed),flush=True)
        if not passed:raise RuntimeError('component contract failed: '+name)
    def record(label,result):
        report['native_requests'].append({'label':label,'status':result.get('status'),
            'reason':result.get('reason'),'research_progress':result.get('research_progress'),
            'native_performance':result.get('performance')})
        write_json(out/'report.json',report)
    try:
        with NativePool(game_data(args.game_dir),out/'workers',resources,runtime_profile='server-large-gen0')as pool:
            base=pool.research_progress_baseline(ctx,out/'baseline',90)
            write_json(out/'research-progress.json',base)
            meta={k:original[k]for k in ('seed','character','ascension','unlocks')}
            contracts,_=pool.run(meta,out/'native-contract',90,fresh=True,disposable=True,_command='progress_contract')
            check('native_progress_contracts',contracts.get('passed')is True,
                report=contracts)
            request=copy.deepcopy(original)
            for key in ('checkpoint','out','command','expected_evidence','probe','card_menu_probe','real_card_menu_choice','capture_card_menu_state'):
                request.pop(key,None)
            request.update(research_progress=base,capture_f1_winners=True,capture_checkpoints=True)
            source,identity=pool.run(request,out/'full-source',180,fresh=True)
            record('full-source',source)
            check('fresh_source_reached_f2',source.get('status')=='TERMINAL'
                and (source.get('f1_winner_reuse')or{}).get('terminal_encounter')
                ==(source.get('f1_winner_reuse')or{}).get('final_second_encounter')
                and bool((source.get('f1_winner_reuse')or{}).get('final_second_encounter')),
                native_identity=identity)
            manager=F1WinnerReuse(True)
            proposals=manager.observe(source,'full-source',request,source_request=request)
            check('unused_complete_f1_winner_available',bool(proposals),count=len(proposals))
            artifact=proposals[0]['request']['f1_winner_proposal']
            eligible=[r for r in source.get('checkpoints',[])if r['prefix_length']<=len(request['history'])]
            check('real_progress_checkpoint_captured',bool(eligible),count=len(eligible))
            cp=max(eligible,key=lambda r:r['prefix_length'])
            payload=read_json(cp['path'])['payload']
            check('checkpoint_has_real_progress',isinstance(payload.get('native_progress_snapshot'),dict))
            # The first task already leaves a full route's discoveries/statistics
            # in this worker. Start another different prefix before the restore.
            contamination={**meta,'history':source['trace'][:min(300,len(source['trace']))],
                'generate_candidate':False,'capture_checkpoints':False}
            dirty,_=pool.run(contamination,out/'different-history',90)
            record('different-history',dirty)
            check('different_history_replayed',dirty.get('status')in('DECISION','TERMINAL'))
            restore=copy.deepcopy(request);restore['checkpoint']=cp['path'];restore['capture_checkpoints']=False
            restored,_=pool.run(restore,out/'restored-source',180)
            record('restored-source',restored)
            check('checkpoint_restore_did_not_fail',restored.get('status')in('TERMINAL','BUDGET','DECISION'),
                restored_prefix=restored.get('restored_prefix'),reason=restored.get('reason'))
            check('checkpoint_restore_marker',(restored.get('research_progress')or{}).get('checkpoint_restored')is True)
            rows=restored.get('f1_winner_candidates')or[]
            check('restored_f1_entry_progress_and_complete_root_equal',bool(rows)and
                canonical(rows[0]['native_progress'])==canonical(artifact['native_progress'])
                and rows[0]['root_state_text']==artifact['root_state_text'])
            consumer,_=pool.run(proposals[0]['request'],out/'strict-consumer',180,fresh=True,disposable=True)
            record('strict-consumer',consumer)
            manager.record_execution(consumer,'strict-consumer',request=proposals[0]['request'])
            diagnostics=manager.snapshot();report['f1_winner_reuse']=diagnostics
            check('unused_plan_actually_won_f1_and_entered_f2',diagnostics['consumer_routes_loaded']==1
                and diagnostics['consumer_native_f1_wins']==1 and diagnostics['consumer_native_f2_entries']==1,
                diagnostics=diagnostics)
            check('consumer_no_additional_f1_search',diagnostics['consumer_f1_search_audits_known']==1
                and diagnostics['consumer_f1_member_search_records']==0)
            report['pool_stats']=pool.stats
        report['passed']=True;write_json(out/'report.json',report)
        return 0
    except Exception as error:
        report['error']=str(error);write_json(out/'report.json',report);print(str(error),flush=True)
        return 1


if __name__=='__main__':raise SystemExit(main())
