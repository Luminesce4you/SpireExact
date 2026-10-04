"""Read a completed A smoke run; no native execution or heuristic win promotion."""
from pathlib import Path
import argparse,json,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.planning.io import read_json,write_json
from dashboard.winning_route import WinningRouteStore


def inspect(folder):
    folder=Path(folder).resolve()
    if not(folder/'baseline-report.json').is_file():
        return {'ready':False,'passed':False,'reason':'run has not completed'}
    manifest=read_json(folder/'validation-manifest.json')
    baseline=read_json(folder/'baseline-report.json')
    target=folder/('seed-'+str(manifest['seed']))
    result=read_json(target/'result.json')
    baseline_path=target/'research-progress-baseline'
    progress_baseline_cost=None
    if baseline_path.is_dir():
        try:
            bp=read_json(baseline_path/'data/performance.json')
            bt=read_json(baseline_path/'transport.json')
            progress_baseline_cost={'native_performance':bp,'transport':bt,
                'scope':'one disposable initialization export, no game actions; separate from source/consumer throughput'}
        except (OSError,ValueError,KeyError):
            progress_baseline_cost={'unknown':True,'scope':'baseline export has incomplete native cost reports'}
    records=[json.loads(line)for line in (target/'evaluations.jsonl').read_text(encoding='utf-8').splitlines()if line.strip()]
    metrics=result.get('search_metrics')or{}
    a=metrics.get('f1_winner_reuse')or{}
    costs=metrics.get('research_costs')or{}
    consumers=[];missing_consumers=[];missing_outcomes=[];consumer_inputs=[]
    for row in records:
        if row.get('kind')!='f1_winner_reuse':continue
        label=row['label'];item=target/label
        try:
            request=read_json(item/'request.json')
            consumer_inputs.append({'label':label,'capture_checkpoints':request.get('capture_checkpoints'),
                'has_checkpoint':'checkpoint'in request})
            decision=read_json(item/'data/decision.json')
        except (OSError,ValueError,KeyError):
            missing_consumers.append(label)
            work=row.get('research_work')or{}
            missing_outcomes.append({'label':label,'classification':row.get('classification'),
                'reason':row.get('reason'),'native_cost_unknown':
                (work.get('metrics')or{}).get('native_wall_us')is None,
                'isolated':work.get('isolated')is True})
            continue
        info=decision.get('f1_winner_reuse')or{}
        deployment=info.get('deployment')or{}
        searches=(decision.get('advisor_metrics')or{}).get('searches')
        encounter=(request.get('f1_winner_proposal')or{}).get('encounter')
        f1_searches=(sum(isinstance(s,dict)and s.get('encounter')==encounter for s in searches)
                     if isinstance(searches,list)and isinstance(encounter,str)else None)
        consumers.append({'label':label,'status':decision.get('status'),'reason':decision.get('reason'),
            'loaded':deployment.get('loaded')is True,'native_f1_won':info.get('native_f1_won')is True,
            'native_f2_entered':info.get('native_f2_entered')is True,'f1_search_records':f1_searches,
            'capture_checkpoints':request.get('capture_checkpoints'),'has_checkpoint':'checkpoint'in request,
            'guard_attempted':info.get('guard_attempted'),'entry_checked':info.get('proposal_entry_checked'),
            'failed_guard':info.get('failed_entry_guard'),'deployment':deployment})
    deployed=[r for r in consumers if r['loaded']and r['native_f1_won']and r['native_f2_entered']and r['f1_search_records']==0]
    sources=[r for r in records if (r.get('research_work')or{}).get('role')in('source_A','source_AB')]
    source_cp=sum(r.get('checkpoint_prefix',0)>0 for r in sources)
    unrecorded=max(0,int(metrics.get('scheduled_evaluations',len(records)))-len(records))
    rates={}
    for role,group in (costs.get('groups')or{}).items():
        measures=group.get('metrics')or{}
        nodes=measures.get('expanded_combat_nodes')or{}
        wall=measures.get('native_wall_us')or{}
        pairs=[]
        for record in records:
            work=record.get('research_work')or{}
            if work.get('role')!=role:continue
            pair=work.get('metrics')or{}
            count,duration=pair.get('expanded_combat_nodes'),pair.get('native_wall_us')
            if type(count)is int and count>=0 and type(duration)is int and duration>0:pairs.append((count,duration))
        matched_nodes=sum(p[0]for p in pairs);matched_us=sum(p[1]for p in pairs)
        rates[role]={'evaluations':group.get('evaluations'),'checkpoint_restores':group.get('checkpoint_restores'),
            'cache_hits':group.get('cache_hits'),'known_nodes':nodes.get('measured_sum'),
            'unknown_node_reports':nodes.get('unknown_evaluations'),'known_native_worker_seconds':(wall.get('measured_sum')or 0)/1e6,
            'unknown_native_wall_reports':wall.get('unknown_evaluations'),
            'matched_node_wall_reports':len(pairs),'matched_nodes':matched_nodes,'matched_native_worker_seconds':matched_us/1e6,
            'nodes_per_matched_native_worker_second':matched_nodes*1e6/matched_us if matched_us>0 else None}
    verified=False;verification=None
    if result.get('status')=='VERIFIED_WIN_IN_NATIVE_HOST':
        document=WinningRouteStore().load(folder/'validation-manifest.json')
        verified=document.get('verified')is True
        verification={k:document.get(k)for k in ('verified','checks','issues')}
    source_group=(costs.get('groups')or{}).get('source_A')or(costs.get('groups')or{}).get('source_AB')or{}
    source_measures=source_group.get('metrics')or{}
    legacy=[r for r in records if r.get('kind')=='paired_card_probe']
    legacy_costs={'batches':len(legacy),'probes':sum(r.get('probes',0)for r in legacy),
        'measured_nodes':sum(r.get('measured_expanded_combat_nodes',0)for r in legacy),
        'measured_native_worker_seconds':sum(r.get('measured_probe_native_seconds',0)for r in legacy),
        'unknown_work_probes':sum(r.get('unknown_work_probes',0)for r in legacy),
        'scope':'existing i070 batches; recorded measured subsets only, separate from new A costs'}
    checks={'healthy_completion':baseline.get('exit_code')in(0,124),
        'different_winner_proposed':a.get('alternative_requests',0)>0,
        'strict_native_f1_win_and_f2_entry':bool(deployed),
        'all_loaded_f1_searches_audited_zero':all(c['f1_search_records']==0 for c in consumers if c['loaded'])and bool(deployed),
        'ordinary_sources_restore_checkpoints':source_cp>0,
        'ordinary_sources_are_not_isolated':bool(sources)and all((r.get('research_work')or{}).get('isolated')is False for r in sources),
        'consumer_checkpoint_inputs_disabled':bool(consumer_inputs)
            and len(consumer_inputs)==len(consumers)+len(missing_consumers)
            and all(not c['has_checkpoint']and c['capture_checkpoints']is False for c in consumer_inputs),
        # A pool timeout can legitimately have no native decision file. Keep
        # its cost unknown and its label/reason in the ledger, rather than
        # treating absence as either zero work or an unaccounted successful run.
        'consumer_outcomes_accounted':all(c['reason']=='QUEUE_TIMEOUT'
            and c['native_cost_unknown']and c['isolated']for c in missing_outcomes),
        'source_cost_measurement_present':all((source_measures.get(k)or{}).get('measured_sum',0)>0 for k in ('native_wall_us','expanded_combat_nodes')),
        'win_certificate_valid_if_present':verified if result.get('status')=='VERIFIED_WIN_IN_NATIVE_HOST'else True}
    return {'ready':True,'passed':all(checks.values()),'checks':checks,'run':folder.name,'seed':manifest['seed'],
        'result_status':result.get('status'),'verified_win':verified,'verification':verification,'wall_seconds':baseline.get('wall_seconds'),
        'records':len(records),'source_records':len(sources),'source_checkpoint_restores':source_cp,
        'unrecorded_scheduled_work_unknown':unrecorded,'a':a,'successful_native_deployments':len(deployed),
        'consumers':consumers,'consumer_inputs':consumer_inputs,'missing_consumer_outputs':missing_consumers,
        'missing_consumer_outcomes':missing_outcomes,
        'consumer_native_output_coverage':{'available':len(consumers),'missing':len(missing_consumers)},
        'costs':costs,'throughput':rates,'existing_i070_costs':legacy_costs,
        'research_progress_baseline_cost':progress_baseline_cost,
        'history_reference_scope':'i047 is an unmatched diagnostic reference; no paired speedup or promotion claim',
        'scope':'functional A smoke only; UNKNOWNs are not proofs of infeasibility'}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('run',type=Path);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();report=inspect(args.run);write_json(args.out,report)
    print(json.dumps({k:report.get(k)for k in ('ready','passed','seed','records','successful_native_deployments','checks')},ensure_ascii=False))
