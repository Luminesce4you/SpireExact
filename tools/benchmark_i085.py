"""Paired i082/i085 native benchmark: plan, run serially on Windows, audit/report.

Planning/reporting use no game DLL. Only `run` launches native work. Historical
certificates are never imported. Missing measurements and incomplete runs stay
explicit. An artifact audit does NOT execute a new independent native replay.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from spire_exact.canonical import canonical, digest, ContractError
from spire_exact.mode1 import check_winning_replay, OBJECTIVE
from spire_exact.planning.io import read_json, write_json
from tools.run_release_source import settings, effective_parameters
from tools.tail_statistics import scaling_report

ARMS = {
    'i082': ('i082', []),
    'i085': ('i085', []),
    'i085-tail': ('i085', ['--tail-mode','on']),
    'i085-core': ('i085', ['--tail-mode','off']),
    'i085-tail-shadow': ('i085', ['--tail-mode','shadow']),
    'i085-tail-wide': ('i085', ['--tail-mode','on','--tail-feedback-rounds','8']),
    'i085-tail-no-cap': ('i085', ['--tail-mode','on','--tail-cohort-inflight','56']),
    'i082-low-threshold': ('i082', ['--gate-model-minimum','8','--gate-model-refresh','4']),
    'i085-routes': ('i085', ['--macro-routes', 'on']),
    'i085-window14': ('i085', ['--dispatch-window', '14']),
    'i085-no-plateau': ('i085', ['--no-macro-plateau']),
    'i085-no-widening': ('i085', ['--no-macro-widening']),
    'i085-no-fairness': ('i085', ['--no-macro-fair']),
    'i085-no-shadow': ('i085', ['--macro-routes', 'off']),
    'i085-root-jitter': ('i085', ['--root-jitter', '4', '--restart-jitter']),
}
BASELINE = '4c5823acd1a68acb797069fb8f7522a6ad857bbf'


def source_hash(root=ROOT):
    """Content identity of actual Python/native source and setup lock files."""
    rows=[]
    for folder in ('spire_exact', 'native', 'tools'):
        for p in sorted((Path(root)/folder).rglob('*')):
            if p.is_file() and p.suffix in ('.py', '.cs', '.csproj', '.patch') and not {'bin','obj','__pycache__'} & set(p.parts):
                rows.append([p.relative_to(root).as_posix(),hashlib.sha256(p.read_bytes()).hexdigest()])
    for name in ('upstream.lock.json','Directory.Build.props','NuGet.Config'):
        p=Path(root)/name
        if p.exists():rows.append([name,hashlib.sha256(p.read_bytes()).hexdigest()])
    return digest(rows)


def finite_number(v):
    return type(v) in (int,float) and math.isfinite(v) and v>=0


def split_csv(value):
    return [v.strip() for v in value.split(',') if v.strip()]


def create_plan(out: Path, seeds, solver_seeds, arms, minutes=30, workers=7):
    if out.exists():raise ValueError('benchmark output must be new; use run/report to resume')
    if not seeds or len(seeds)!=len(set(seeds)) or any(not re.fullmatch(r'[A-Za-z0-9]{1,64}',s) for s in seeds):
        raise ValueError('distinct alphanumeric game seeds required')
    if not solver_seeds or len(solver_seeds)!=len(set(solver_seeds)) or any(type(s) is not int for s in solver_seeds):
        raise ValueError('distinct integer solver seeds required')
    if not arms or len(arms)!=len(set(arms)) or any(a not in ARMS for a in arms):raise ValueError('invalid arms')
    if type(minutes) is not int or not 1<=minutes<=180 or type(workers) is not int or not 1<=workers<=7:
        raise ValueError('minutes 1..180 and workers 1..7 required')
    # Cycle condition order across paired blocks; do not run two arms together.
    rows=[]
    frozen_hash=source_hash()
    for block,(seed,solver_seed) in enumerate((s,k) for s in seeds for k in solver_seeds):
        offset=block%len(arms)
        order=list(arms[offset:])+list(arms[:offset])
        for arm in order:
            profile,extra=ARMS[arm]
            extra=['--gate-timing',*extra]
            run_id=f'{seed}-s{solver_seed}-{arm}'
            run_out=(out/'runs'/run_id).resolve()
            args=['--out',str(run_out),*settings(seed,minutes,workers,solver_seed,profile),*extra]
            effective=effective_parameters(args)
            command=[sys.executable,str(ROOT/'tools/run_release_source.py'),'--seed',seed,
                     '--out',str(run_out),'--feature-profile',profile,
                     '--research-minutes' if minutes>45 else '--minutes',str(minutes),
                     '--workers',str(workers),'--solver-seed',str(solver_seed)]
            if extra:command+=['--extra',*extra]
            rows.append({'id':run_id,'seed':seed,'solver_seed':solver_seed,'arm':arm,
                'feature_profile':profile,'command':command,'out':str(run_out),
                'wall_cap_seconds':minutes*60,'effective_parameters':effective,'source_hash':frozen_hash})
    plan={'schema':'spire-i085-benchmark/v1','created_utc':datetime.now(timezone.utc).isoformat(),
          'source_hash':frozen_hash,'baseline_commit':BASELINE,'root':str(ROOT),
          'context':{'character':'IRONCLAD','ascension':10,'unlocks':'all','fresh_start':True,'objective':OBJECTIVE},
          'seeds':list(seeds),'solver_seeds':list(solver_seeds),'arms':list(arms),'workers':workers,
          'wall_cap_seconds':minutes*60,'runs':rows,'native_executed_during_plan':False,
          'interpretation':'same source checkout, i082 switches off; rotate arm order by paired seed/solver-seed block'}
    out.mkdir(parents=True)
    write_json(out/'plan.json',plan)
    return plan


def load_ledger(folder, result):
    path=folder/'evaluations.jsonl'
    if not path.exists():return result.get('evaluations',[]),['ledger_missing_snapshot_used']
    rows=[];notes=[]
    with path.open(encoding='utf-8-sig') as f:
        for n,line in enumerate(f,1):
            try:
                row=json.loads(line)
                if not isinstance(row,dict):raise ValueError('not object')
                rows.append(row)
            except (ValueError,TypeError):notes.append(f'invalid_ledger_line:{n}')
    return rows,notes


def recorded_win_audit(folder: Path, result: dict):
    """Require the *existing* independent replay, its request and both outputs.

    Certificate/route JSON alone is insufficient. This repeats the project's
    artifact consistency contract, NOT native execution or external attestation.
    """
    if result.get('status')!='VERIFIED_WIN_IN_NATIVE_HOST':
        return {'verified_evidence':False,'reason':'no_verified_status'}
    try:
        certificate=read_json(folder/'certificate.json')
        route=read_json(folder/'winning-route.json')
        if canonical(route['context'])!=canonical(result['context']):raise ValueError('route context mismatch')
        if digest(route['trace'])!=certificate['trace_sha256']:raise ValueError('route digest mismatch')
        if result['context'].get('objective')!=OBJECTIVE:raise ValueError('objective mismatch')
        errors=[]
        for verify in sorted(folder.glob('verify-eval-*')):
            try:
                request=read_json(verify/'request.json')
                if (request.get('command')!='replay' or request.get('generate_candidate') is not False
                    or any(k in request for k in ('advisor','checkpoint','probe','card_menu_probe',
                        'map_route_plan','f1_winner_proposal','real_card_menu_choice'))):
                    raise ValueError('verification was not a fresh no-advisor replay request')
                if canonical(request['history'])!=canonical(route['trace']):raise ValueError('verification history mismatch')
                for k in ('seed','character','ascension','unlocks'):
                    if request.get(k)!=result['context'].get(k):raise ValueError('verification context mismatch')
                label=verify.name[len('verify-'):]
                candidate_folder=folder/label
                cached=candidate_folder/'cached.json'
                if cached.exists():
                    payload=read_json(cached);candidate=payload['result'];candidate_native=payload['identity']
                else:
                    candidate=read_json(candidate_folder/'data/decision.json')
                    candidate_native=read_json(candidate_folder/'data/identity.json')
                replay=read_json(verify/'data/decision.json')
                replay_native=read_json(verify/'data/identity.json')
                expected=check_winning_replay(candidate,replay,
                    {'context':result['context'],'native':candidate_native},
                    {'context':result['context'],'native':replay_native})
                if canonical(expected)!=canonical(certificate):raise ValueError('certificate mismatch')
                if canonical(request.get('expected_evidence'))!=canonical(candidate['decision_evidence']):
                    raise ValueError('expected evidence mismatch')
                return {'verified_evidence':True,'reason':'independent_replay_artifacts_match_contract',
                        'candidate_label':label,'native_identity':replay_native,
                        'new_native_replay_executed_by_report':False}
            except (OSError,ValueError,TypeError,KeyError,ContractError) as error:
                errors.append(verify.name+':'+str(error))
        return {'verified_evidence':False,'reason':'independent_replay_evidence_missing_or_invalid','details':errors}
    except (OSError,ValueError,TypeError,KeyError,ContractError) as error:
        return {'verified_evidence':False,'reason':'certificate_or_route_invalid','details':[str(error)]}


def work_summary(records):
    by_kind={}
    for row in records:
        kind=str(row.get('kind','unknown'))
        bucket=by_kind.setdefault(kind,{'evaluations':0,'cache_hits':0,'native_seconds_measured':0.,
            'native_seconds_unknown':0,'combat_nodes_measured':0,'combat_nodes_unknown':0,
            'beam_seconds_measured':0.,'beam_seconds_unknown':0,'prefix_seconds_measured':0.,
            'prefix_seconds_unknown':0,'checkpoint_restores':0})
        bucket['evaluations']+=1
        cached=row.get('cache_hit') is True
        bucket['cache_hits']+=int(cached)
        bucket['checkpoint_restores']+=int((row.get('checkpoint_prefix') or 0)>0)
        synthetic=row.get('synthetic') is True or row.get('classification')=='SYNTHETIC_PROBE'
        perf=row.get('performance') or {}
        if cached:wall=0.
        elif synthetic:wall=row.get('probe_native_seconds')
        else:
            raw=perf.get('wall_us');wall=raw/1e6 if finite_number(raw) else None
        if finite_number(wall):bucket['native_seconds_measured']+=wall
        else:
            bucket['native_seconds_unknown']+=1
            partial=row.get('measured_probe_native_seconds') if synthetic else None
            if finite_number(partial):bucket['native_seconds_measured']+=partial
        reported=(row.get('research_work') or {}).get('metrics') or {}
        # The old generic record can use 0 as a fallback for missing searches.
        # Prefer the explicit research-work missingness contract when supplied.
        nodes=(0 if cached else reported['expanded_combat_nodes'] if 'expanded_combat_nodes' in reported
               else row.get('expanded_combat_nodes'))
        if finite_number(nodes):bucket['combat_nodes_measured']+=nodes
        else:
            bucket['combat_nodes_unknown']+=1
            partial=row.get('measured_expanded_combat_nodes') if synthetic else None
            if finite_number(partial):bucket['combat_nodes_measured']+=partial
        for metric,stage in (('beam_seconds','beam_search'),('prefix_seconds','prefix_native_execute')):
            raw=((perf.get('exclusive_stages') or {}).get(stage) or {}).get('us')
            value=0 if cached else raw/1e6 if finite_number(raw) else None
            if finite_number(value):bucket[metric+'_measured']+=value
            else:bucket[metric+'_unknown']+=1
        for name in ('prefix_replayed_actions','checkpoint_skipped_actions','new_actions'):
            value=0 if cached else (perf.get('counters') or {}).get(name)
            bucket.setdefault(name+'_measured',0);bucket.setdefault(name+'_unknown',0)
            if finite_number(value):bucket[name+'_measured']+=value
            else:bucket[name+'_unknown']+=1
    return by_kind


def inspect_run(spec):
    folder=Path(spec['out'])
    base={k:spec[k] for k in ('id','seed','solver_seed','arm','wall_cap_seconds')}
    if not (folder/'result.json').exists():
        status_file=folder.parent/(folder.name+'.benchmark-status.json')
        status=read_json(status_file) if status_file.exists() else {}
        return {**base,'available':False,'verified_evidence':False,
                'run_state':'started_without_result' if status else 'not_run', 'runner_status':status}
    try:result=read_json(folder/'result.json')
    except (OSError,ValueError) as e:return {**base,'available':False,'run_state':'unreadable_result','error':str(e)}
    records,notes=load_ledger(folder,result)
    audit=recorded_win_audit(folder,result)
    timing={}
    try:timing=read_json(folder/'verification-timing.json')
    except (OSError,ValueError):pass
    exact=timing.get('verified_wall_seconds') if (audit['verified_evidence'] and
        timing.get('candidate_label')==audit.get('candidate_label')) else None
    if not finite_number(exact):exact=None
    candidate=next((r.get('completed_wall_seconds') for r in records
                   if r.get('label')==audit.get('candidate_label')),None)
    if not finite_number(candidate):candidate=None
    final=result.get('elapsed_seconds')
    if not finite_number(final):final=None
    if exact is not None and (candidate is not None and exact<candidate or final is not None and exact>final):
        notes.append('verification_timestamp_outside_candidate_final_interval');exact=None
    metrics=result.get('search_metrics') or {}
    costs=work_summary(records)
    classes=Counter(str(r.get('classification','unknown')) for r in records)
    earliest={}
    for act in (1,2):
        times=[r['completed_wall_seconds'] for r in records if r.get('classification')!='SYNTHETIC_PROBE'
            and (r.get('observation') or {}).get('act',-1)>=act and finite_number(r.get('completed_wall_seconds'))]
        earliest['first_result_reporting_act'+str(act+1)]=min(times) if times else None
    identities=[]
    for identity_file in sorted(folder.glob('eval-*/data/identity.json')):
        try:
            value=read_json(identity_file)
            if value not in identities:identities.append(value)
        except (ValueError,OSError):notes.append('unreadable_native_identity:'+identity_file.parent.name)
    if not identities and audit.get('native_identity'):identities=[audit['native_identity']]
    complete=result.get('stop_reason') in ('verified_boolean_maximum','time_budget','evaluation_budget')
    context_ok=all(result.get('context',{}).get(k)==v for k,v in
                   (('seed',spec['seed']),('character','IRONCLAD'),('ascension',10),('unlocks','all'),('objective',OBJECTIVE)))
    # The public launch manifest binds the actual settings and resource request.
    # Do not accept hand-copied results from another run as a paired experiment.
    settings_errors=[]
    try:
        manifest=read_json(folder.parent/(folder.name+'.validation-manifest.json'))
        wanted=spec['effective_parameters'];actual=manifest['effective_parameters']
        for key,value in wanted.items():
            # game-dir is resolved by the source launcher, not needed in dry planning.
            if key!='game_dir' and actual.get(key)!=value:settings_errors.append('manifest:'+key)
        if (manifest.get('seed')!=spec['seed'] or manifest.get('solver_seed')!=spec['solver_seed']
                or manifest.get('wall_cap_seconds')!=spec['wall_cap_seconds']
                or manifest.get('fresh_start') is not True):settings_errors.append('manifest:context_or_budget')
        resources=result.get('resources') or {}
        if resources.get('workers')!=wanted.get('workers') or resources.get('dop')!=wanted.get('dop'):
            settings_errors.append('resources:workers_or_dop')
        if (result.get('configuration') or {}).get('solver_seed')!=spec['solver_seed']:
            settings_errors.append('configuration:solver_seed')
    except (OSError,ValueError,KeyError,TypeError):settings_errors.append('launch_manifest_missing_or_invalid')
    job={};runner={}
    try:job=read_json(folder.parent/(folder.name+'-resources.json'))
    except (OSError,ValueError):settings_errors.append('job_resource_report_missing')
    try:runner=read_json(folder.parent/(folder.name+'.benchmark-status.json'))
    except (OSError,ValueError):settings_errors.append('benchmark_runner_report_missing')
    if job:
        if (job.get('enforced') is not True or job.get('wall_limit_seconds')!=spec['wall_cap_seconds']
            or job.get('workload_limit_bytes')!=14336*1024**2 or job.get('logical_cpu_count')!=8):
            settings_errors.append('job_resource_protocol_mismatch')
    if runner:
        if runner.get('state')!='finished':settings_errors.append('runner_not_finished')
        if any(runner.get(k)!=spec.get('source_hash') for k in ('source_hash_before','source_hash_after')):
            settings_errors.append('source_changed_or_unrecorded')
    resource_valid=(not settings_errors and not job.get('storage_limit'))
    run_clean=runner.get('returncode')==0 and not job.get('hard_timeout') and resource_valid
    # A real proof after a watchdog overrun remains proof, not a budget success.
    budget_win=(exact<=spec['wall_cap_seconds'] and run_clean) if audit['verified_evidence'] and exact is not None else (
        False if not audit['verified_evidence'] else None)
    return {**base,'available':True,'run_state':'completed' if complete else 'partial',
            'context_matches_plan':context_ok,'settings_match_plan':not settings_errors,'settings_errors':settings_errors,
            'within_coordinator_budget_win':budget_win,'job_resources':job,'runner_status':runner,
            'resource_valid':resource_valid,'status':result.get('status'),'stop_reason':result.get('stop_reason'),
            **audit,'time_to_first_verified_win_seconds':exact,
            'candidate_seconds':candidate,'verified_final_report_upper_seconds':final if audit['verified_evidence'] else None,
            'elapsed_seconds':final,'native_identities':identities,'notes':notes,
            'classifications':dict(classes),'evaluations':len(records),'costs_by_kind':costs,
            'gate_entries':(result.get('gate_models') or {}).get('gates',[]),
            'verification_failures':len(list(folder.glob('verify-*/verification-failed.json'))),
            'milestones':earliest,'search_metrics':{k:metrics.get(k) for k in
                ('unique_strategic_routes','unique_room_routes','prefix_cache_hits','prefix_cache_misses',
                 'fresh_routes','macro_plateaus','recover_deferred','deferred_pending','deferred_promoted',
                 'macro_service','macro_routes','f2_readiness','preparation','tail_search','gate_timing')},
            'timing_scope':'coordinator wall origin; milestone times are result availability, not exact in-rollout crossing times'}


def compare_rows(rows, arms):
    indexed={(r['seed'],r['solver_seed'],r['arm']):r for r in rows}
    pairs=[]
    if 'i082' not in arms:return pairs
    for arm in arms:
        if arm=='i082':continue
        included=[];omitted=[]
        for a in rows:
            if a['arm']!='i082':continue
            b=indexed.get((a['seed'],a['solver_seed'],arm))
            if b is None:continue
            reason=None
            for r in (a,b):
                if not r.get('available') or r.get('run_state')!='completed':reason='missing_or_partial'
                elif not r.get('context_matches_plan'):reason='context_mismatch'
                elif not r.get('settings_match_plan'):reason='settings_or_resources_mismatch'
                elif r.get('resource_valid') is False:reason='resource_or_storage_protocol_invalid'
                elif r.get('status')=='VERIFIED_WIN_IN_NATIVE_HOST' and not r.get('verified_evidence'):reason='verification_artifact_audit_failed'
                elif r.get('verified_evidence') and r.get('within_coordinator_budget_win') is None:reason='verified_time_unknown'
                elif len(r.get('native_identities',[]))!=1:reason='native_identity_unknown_or_mixed'
            if reason is None and a['native_identities']!=b['native_identities']:reason='native_identity_mismatch'
            if reason is None and a['wall_cap_seconds']!=b['wall_cap_seconds']:reason='budget_mismatch'
            partition_fields=('cpu_affinity','logical_cpu_count','physical_core_count','smt_used','efficiency_class','workload_limit_bytes')
            if reason is None and any((a.get('job_resources') or {}).get(k)!=(b.get('job_resources') or {}).get(k) for k in partition_fields):
                reason='resource_partition_mismatch'
            if reason:
                omitted.append({'seed':a['seed'],'solver_seed':a['solver_seed'],'reason':reason});continue
            cap=a['wall_cap_seconds']
            def observed(r):
                return r.get('time_to_first_verified_win_seconds') if r.get('verified_evidence') else None
            at,bt=observed(a),observed(b)
            timed=all(not r.get('verified_evidence') or finite_number(observed(r)) for r in (a,b))
            included.append({'seed':a['seed'],'solver_seed':a['solver_seed'],
                'i082_win':a.get('within_coordinator_budget_win') is True,
                'candidate_win':b.get('within_coordinator_budget_win') is True,
                'restricted_time_delta_seconds':((min(bt,cap) if b.get('within_coordinator_budget_win') is True else cap)-
                     (min(at,cap) if a.get('within_coordinator_budget_win') is True else cap)) if timed else None})
        deltas=[p['restricted_time_delta_seconds'] for p in included if p['restricted_time_delta_seconds'] is not None]
        per_seed=defaultdict(list)
        for p in included:
            if p['restricted_time_delta_seconds'] is not None:per_seed[p['seed']].append(p['restricted_time_delta_seconds'])
        seed_values=[statistics.mean(v) for v in per_seed.values()]
        ci=None
        if len(seed_values)>=2:
            rng=random.Random(8505)
            boot=sorted(statistics.mean(rng.choices(seed_values,k=len(seed_values))) for _ in range(2000))
            ci=[boot[49],boot[1949]]
        pairs.append({'candidate':arm,'auditable_pairs':len(included),'omitted_pairs':omitted,
            'i082_only_wins':sum(p['i082_win'] and not p['candidate_win'] for p in included),
            'candidate_only_wins':sum(p['candidate_win'] and not p['i082_win'] for p in included),
            'both_wins':sum(p['candidate_win'] and p['i082_win'] for p in included),
            'neither_win':sum(not p['candidate_win'] and not p['i082_win'] for p in included),
            'timed_pairs':len(deltas),'paired_mean_restricted_time_delta_seconds':statistics.mean(deltas) if deltas else None,
            'seed_cluster_bootstrap_interval':ci,'seed_clusters_for_interval':len(seed_values),
            'pairs':included,'scope':'candidate minus i082; failures capped at shared wall budget; missing win times omitted, not replaced by candidate time'})
    return pairs


def report(out):
    plan=read_json(out/'plan.json')
    rows=[inspect_run(spec) for spec in plan['runs']]
    comparisons=compare_rows(rows,plan['arms'])
    result={'schema':'spire-i085-comparison/v1','plan_source_hash':plan['source_hash'],'runs':rows,
            'comparisons':comparisons,'time_scaling':scaling_report(rows,plan['arms']),'new_native_execution_by_report':False,
            'warnings':['Do not treat missing native metrics as zero.',
                'One-step shadow paths have no counterfactual outcome labels.',
                'No-DLL tests and historical wins are not i085 native evidence.',
                'Repeated solver seeds on one game seed are not independent game seeds.']}
    write_json(out/'comparison.json',result)
    lines=['# i082 / i085 paired native comparison','',
        'This report audits recorded native replay artifacts. Reporting itself executes no native replay.',
        '', '| Run | State | Audited verified win | First verified seconds | Native seconds (known only) | Unknown cost rows |',
        '|---|---|---:|---:|---:|---:|']
    for row in rows:
        cost=row.get('costs_by_kind',{})
        measured=sum(v['native_seconds_measured'] for v in cost.values())
        unknown=sum(v['native_seconds_unknown'] for v in cost.values())
        shown=f'{measured:.3f}' if row.get('available') and cost else 'unknown'
        missing=str(unknown) if row.get('available') and cost else 'unknown'
        lines.append(f"| {row['id']} | {row.get('run_state')} | {row.get('verified_evidence',False)} | {row.get('time_to_first_verified_win_seconds')} | {shown} | {missing} |")
    lines+=['','## Paired results','', 'Negative restricted-time deltas favor the candidate. Unsolved runs use the common time cap; missing wins timestamps are not imputed.','']
    for comparison in comparisons:
        lines += [f"### {comparison['candidate']}", '',
          f"Auditable pairs: {comparison['auditable_pairs']}; candidate-only wins: {comparison['candidate_only_wins']}; i082-only wins: {comparison['i082_only_wins']}.",
          f"Mean restricted-time delta: {comparison['paired_mean_restricted_time_delta_seconds']} s; seed-cluster interval: {comparison['seed_cluster_bootstrap_interval']}.",
          f"Omitted pairs: {len(comparison['omitted_pairs'])}. See JSON for exact reasons.",'']
    lines+=['## Detailed evidence','',
        '`comparison.json` contains per-kind native/beam/prefix costs with missingness, exact gate-entry counts, F1/F2 records, cache statistics, macro/route telemetry, resource classifications and verification failures.',
        '', 'Stage crossings are timed at completed-result availability, not at the unrecorded instant inside a rollout.',
        'Synthetic samples never count as gate passes or verified wins. Empty or interrupted runs remain listed.',
        'Intervals with few game seeds are descriptive and should not be used to declare a production improvement.']
    (out/'comparison.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return result


def execute(out):
    if os.name!='nt':raise ValueError('native benchmark requires Windows; plan and report work without native dependencies')
    plan=read_json(out/'plan.json')
    if plan['source_hash']!=source_hash():raise ValueError('source changed since planning; create a new plan')
    with (out/'runner.lock').open('x',encoding='utf-8') as f:f.write(str(os.getpid()))
    try:
        for spec in plan['runs']:
            destination=Path(spec['out']);status_file=destination.parent/(destination.name+'.benchmark-status.json')
            if status_file.exists():continue  # preserve failed/interrupted evidence too
            if destination.exists():raise ValueError('unregistered existing run: '+str(destination))
            destination.parent.mkdir(parents=True,exist_ok=True)
            current_hash=source_hash()
            if current_hash!=plan['source_hash']:raise ValueError('source changed between arms; stop this experiment')
            state={'state':'running','started_utc':datetime.now(timezone.utc).isoformat(),'command':spec['command'],
                   'source_hash_before':current_hash}
            write_json(status_file,state)
            start=time.perf_counter()
            with status_file.with_suffix('.log').open('x',encoding='utf-8') as log:
                completed=subprocess.run(spec['command'],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=False)
            state.update(state='finished',returncode=completed.returncode,wrapper_seconds=time.perf_counter()-start,
                         source_hash_after=source_hash())
            write_json(status_file,state)
            report(out)
    finally:
        (out/'runner.lock').unlink(missing_ok=True)
    report(out)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('plan');a.add_argument('--out',type=Path,required=True)
    a.add_argument('--seeds',default='101');a.add_argument('--solver-seeds',default='271828')
    a.add_argument('--arms',default='i082,i085,i085-routes');a.add_argument('--minutes',type=int,default=30)
    a.add_argument('--workers',type=int,default=7)
    for command in ('run','report'):
        a=sub.add_parser(command);a.add_argument('--out',type=Path,required=True)
    args=p.parse_args(argv);out=args.out.resolve()
    try:
        if args.command=='plan':
            create_plan(out,split_csv(args.seeds),[int(v) for v in split_csv(args.solver_seeds)],
                        split_csv(args.arms),args.minutes,args.workers)
            print(json.dumps({'plan':str(out/'plan.json'),'native_executed':False}))
        elif args.command=='run':execute(out)
        else:
            report(out);print(json.dumps({'report':str(out/'comparison.md'),'native_executed':False}))
    except (OSError,ValueError,KeyError) as error:p.error(str(error))


if __name__=='__main__':main()
