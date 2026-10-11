"""i054a macro search orchestrator.

The historical import surface is preserved:
    from spire_exact.planning.search import SearchConfig, Evaluator, solve

Configuration validation lives in search_config.py; native evaluation, cache,
checkpointing and independent verification live in evaluation.py.
The scheduling and result-emission algorithm remains unchanged from frozen
holdout-01. Do not silently change its policy while reorganising modules.
"""
from __future__ import annotations
from collections import deque
from pathlib import Path
from time import perf_counter

from ..canonical import canonical, ContractError
from ..mode1 import OBJECTIVE, is_winning_candidate
from .archive import (DiverseFrontier, utility, classify_failure,
                      failure_combat_prefix, combat_loss_progress)
from .evaluation import Evaluator
from .io import write_json
from .policies import NATIVE
from .pool import NativePool
from .probes import gate_entries, probe_request, edit_candidates, offered_cards
from .search_config import SearchConfig

def near_miss(result,percent):
    """Allocation signal only: was the fatal fight close enough to re-solve?"""
    progress=combat_loss_progress(result)
    if progress.get('revivals_observed',0)>0:return True
    fraction=progress.get('current_life_hp_removed_fraction')
    return bool(progress.get('available')) and fraction is not None and 100*fraction>=percent

def solve(ctx: dict,out: Path,pool: NativePool,config: SearchConfig,*,advisor=None,initial_prefix=None,
          import_checkpoints=(),progress=None,scope_prefix=None,stop_floor=None):
    from collections import Counter
    from .strategy import StrategicScheduler,DepthFirstScheduler,FailureAnalyzer,strategic_trace
    from .native_tree import NativeTreeScheduler
    from .incumbent import IncumbentScheduler
    from .repairs import RepairQueue,deep_repair
    from .root_portfolio import root_requests
    from .indexed_strategy import IndexedStrategicScheduler
    from .focus import FocusScheduler
    from .gatemodel import GateModels
    if ctx.get('objective')!=OBJECTIVE:raise ContractError('P5 supports mode1 full-information Boolean objective only')
    if (out/'result.json').exists():raise ContractError('use a fresh output directory')
    out.mkdir(parents=True,exist_ok=True)
    if scope_prefix is not None:
        if config.scheduler!='weighted' or config.root_portfolio!=1:raise ContractError('Diagnostic scope requires indexed weighted scheduling and a single root')
        if canonical(initial_prefix or [])!=canonical(scope_prefix):raise ContractError('Diagnostic initial prefix must equal scope prefix')
    if config.root_policies and (initial_prefix or scope_prefix is not None or stop_floor is not None or config.root_portfolio!=1):
        raise ContractError('root policies need a fresh, unscoped start and a single native root')
    ev=(Evaluator(pool,ctx,out,config,advisor) if scope_prefix is None and stop_floor is None else
        Evaluator(pool,ctx,out,config,advisor,scope_prefix=scope_prefix,stop_floor=stop_floor))
    ev.load_archive(import_checkpoints)
    frontier=DiverseFrontier(limit=config.archive_entries)
    # Per-seed gate model: fitted on this run's own native boss fights, used to
    # order options (focus scheduler) and, with config.prior, as rollout tiers.
    probing=bool(config.gate_probe_schedule)
    if probing and (scope_prefix is not None or stop_floor is not None or advisor is None):
        raise ContractError('gate probes need an unscoped search with the combat advisor')
    models=(GateModels(config.solver_seed,probes=True) if probing else GateModels(config.solver_seed)) if probing or config.prior or config.scheduler=='focus' else None
    if config.prior:ev.prior_provider=models.prior
    scheduler=(IncumbentScheduler() if config.scheduler=='incumbent' else NativeTreeScheduler(ctx) if config.scheduler=='tree' else
               DepthFirstScheduler() if config.scheduler=='dfs' else
               FocusScheduler(scope_prefix,models=models,elites=config.focus_elites,pool=config.focus_pool,
                              focus=config.focus_share,site_cap=config.site_cap,cluster_cap=config.focus_cluster_cap,
                              cluster_percent=config.focus_cluster_percent,family=config.focus_family,
                              optimism=config.focus_optimism,carry=config.focus_carry) if config.scheduler=='focus' else
               IndexedStrategicScheduler(scope_prefix))
    repairs=RepairQueue(config.repair_mode);urgent=deque();seen_probes=set();seen_rollouts=set();groups_report=[];failures=[]
    retry_levels={}
    # evaluation label -> root policy of its lineage (only labels that are not native)
    families={};portfolio=(NATIVE,*config.root_policies);restarts=0
    # Synthetic gate probes: per boss the lost real entry that came closest (the
    # base of the next table), tables already scheduled, cards this seed offered.
    prefix_trie=getattr(getattr(scheduler,'explorer',scheduler),'prefixes',None)
    if probing and prefix_trie is None:raise ContractError('gate probes need a scheduler with the exact prefix trie')
    gate_bases={};gate_tables=Counter();offered=Counter()

    def probe_specs(gate_id,table):
        """One table as evaluation-sized batches: the unedited base first, then every one-card edit."""
        base=gate_bases[gate_id]
        trace=prefix_trie.restore(base['node'])+[base['enter']]
        entry={'enter':len(trace)-1};template=ev.request([])
        candidates=[(None,[])]+edit_candidates(base['entry'],offered,config.gate_probe_cards)
        specs=[]
        for start in range(0,len(candidates),config.gate_probe_chunk):
            chunk=candidates[start:start+config.gate_probe_chunk]
            specs.append({'kind':'gate_probe','category':'probe','gate':gate_id,'table':table,'base':base['label'],
                'labels':[name for name,_ in chunk],
                'requests':[probe_request(template,trace,entry,edits,advisor_patch=config.gate_probe_plan)for _,edits in chunk],
                'request':{'history':trace[:-1],'generate_candidate':False}})
        return specs
    best=None;best_label=None;certificate=None
    lanes=max(1,getattr(pool.resources,'workers',1))
    branches=set();rooms=set();completed=0;terminal=0;fresh=0;act2=0;act3=0
    scheduled=Counter()
    result={'schema':'spire-p5-result/v1','context':ctx,'configuration':config.__dict__,
        'resources':pool.resources.as_dict(),'native_runtime':getattr(pool,'runtime',None),'status':'UNKNOWN','lower_bound':0,'upper_bound':1,
        'optimal_in_backend':False,'game_equivalence_verified':False,
        'semantic_scope':'bundled native DLL commands in patched offline TestMode',
        'information_mode':'full; NOT a first-attempt public-information evaluation',
        'heuristic_pruning':True,'exclusion_certificates':[], 'evaluations':[]}
    if scope_prefix is not None or stop_floor is not None:
        result['diagnostic_scope']={'prefix_length':len(scope_prefix or []),'stop_floor':stop_floor,'counts_as_fresh_planner_run':False}

    def absorb(items):
        nonlocal best,best_label,certificate,completed,terminal,fresh,act2,act3
        for spec,candidate,label in items:
            if spec['kind']=='gate_probe':
                # Synthetic outcomes go to the gate model's probe tables and nowhere else.
                models.add_probes(spec['gate'],spec['table'],list(zip(spec['labels'],candidate['outcomes'])))
                if progress:progress({'label':label,'kind':'gate_probe','classification':'SYNTHETIC_PROBE','floor':None,'restored_prefix':0})
                continue
            classification=classify_failure(candidate)
            obs=candidate.get('observation') or {}
            family=spec.get('family')
            if family:families[label]=family
            if candidate.get('trace'):
                completed+=1;branches.add(strategic_trace(candidate));rooms.add(strategic_trace(candidate,True))
                act2+=int(obs.get('act',0)>=1);act3+=int(obs.get('act',0)>=2)
                fresh+=int(not spec['request']['history'] or spec['kind']=='root_rollout')
            terminal+=int(classification in ('NATIVE_ROUTE_DEATH','NATIVE_WIN_CANDIDATE'))
            if models is not None:
                # The parent is the trajectory this request deviated from (or
                # re-solved); the pair feeds the difference model.
                models.add(candidate,label,(spec.get('group')or spec.get('repair')or{}).get('source'))
            frontier.add(candidate,label);scheduler.add(candidate,label)
            if probing and candidate.get('trace'):
                searched=len(spec['request']['history'])
                offered.update(offered_cards(candidate,since=searched))
                for entry in gate_entries(candidate):
                    # Only fights this evaluation searched itself, and only lost ones:
                    # a table asks what would have helped the entry that came closest.
                    if entry['index']<searched or entry['lost'] is None:continue
                    known=gate_bases.get(entry['gate'])
                    if known is None or entry['lost'][0]>known['removed']:
                        gate_bases[entry['gate']]={'removed':entry['lost'][0],'node':prefix_trie.index(candidate['trace'][:entry['enter']])[-1],
                            'enter':candidate['trace'][entry['enter']],'entry':entry['entry'],'label':label}
                for gate_id,gate in sorted(models.gates.items()):
                    done=gate_tables[gate_id]
                    if done<len(config.gate_probe_schedule) and len(gate.entries)>=config.gate_probe_schedule[done] and gate_id in gate_bases:
                        gate_tables[gate_id]+=1;urgent.extend(probe_specs(gate_id,done))
            if best is None or utility(candidate)>utility(best):best,best_label=candidate,label
            if classification=='NATIVE_ROUTE_DEATH':
                probe=failure_combat_prefix(candidate)
                failures.append({'label':label,**FailureAnalyzer.analyze(candidate),
                    'entry_prefix_length':len(probe['prefix']) if probe else None})
                if probe and config.scheduler!='dfs':
                    if config.repair_mode=='gate':
                        # Re-solve the fatal fight with the next tactical plan;
                        # a fight that stays fatal moves on to the next level.
                        # Best trajectory first; a failed retry proves nothing.
                        # The exact prefix trie names the entry when the
                        # scheduler has one (full action bytes, no digest).
                        trie=getattr(getattr(scheduler,'explorer',scheduler),'prefixes',None)
                        key=trie.index(probe['prefix'])[-1] if trie is not None else canonical(probe['prefix'])
                        level=retry_levels.get(key,0)
                        if level<len(config.gate_retry_plans) and near_miss(candidate,config.gate_retry_percent):
                            retry_levels[key]=level+1
                            repairs.append({'kind':'gate_retry','category':'failure','priority':utility(candidate),'family':family,
                                'repair':{'source':label,'floor':probe['floor'],'room':probe['entry_observation'].get('room'),'level':level+1},
                                'request':ev.request(probe['prefix'],policy=0,horizon=probe['floor']+config.lookahead_floors,
                                    actions=min(config.max_decisions,len(probe['prefix'])+config.lookahead_actions),
                                    advisor_patch=config.gate_retry_plans[level])})
                    elif (key:=canonical(probe['prefix'])) not in seen_probes:
                        seen_probes.add(key)
                        deep=deep_repair(config.repair_mode,probe['entry_observation'])
                        repairs.append({'kind':'combat_probe','category':'failure','family':family,
                            'repair':{'source':label,'floor':probe['floor'],'room':probe['entry_observation'].get('room'),'deep':deep},
                            'request':ev.request(probe['prefix'],policy=0,horizon=probe['floor'],
                                actions=min(config.max_decisions,len(probe['prefix'])+config.lookahead_actions),advisor_scale=3,
                                boss_budget_ms=config.boss_repair_ms if deep else None)})
            if spec['kind']=='combat_probe' and classification in ('SEARCH_BUDGET','DECISION_BOUNDARY'):
                prefix=candidate.get('trace',[]);key=canonical(prefix)
                if prefix and len(prefix)<config.max_decisions and key not in seen_rollouts:
                    seen_rollouts.add(key);urgent.append({'kind':'probe_followup','category':'preparation','family':family,'request':ev.request(prefix)})
            if is_winning_candidate(candidate):
                certificate=ev.verify_win(candidate,label)
                if certificate:break
            if progress:progress({'label':label,'kind':spec['kind'],'classification':classification,
                'floor':obs.get('floor'),'restored_prefix':candidate.get('restored_prefix',0)})

    last_saved_records=0
    def save(force=False):
        nonlocal last_saved_records
        if not force and not certificate and len(ev.records)-last_saved_records<config.snapshot_stride:return
        last_saved_records=len(ev.records)
        invalid={'UNKNOWN','MECHANISM_OR_HOST_GAP','RESTORE_OR_REPLAY_MISMATCH','TIMEOUT','RESOURCE_LIMIT','NATIVE_CRASH','INVALID_STATE','SEARCH_CANCELLED','SYNTHETIC_PROBE'}
        metrics={**scheduler.snapshot(),'scheduled_evaluations':ev.serial,'proposed_evaluations':sum(scheduled.values()),'inflight_evaluations':ev.pending_count,
            'executed_evaluations':sum(not r.get('cache_hit') for r in ev.records),
            'valid_evaluations':sum(r.get('classification') not in invalid for r in ev.records),
            'candidates_completed':completed,'complete_games':terminal,'fresh_start_candidates':fresh,
            'unique_strategic_routes':len(branches),'unique_room_routes':len(rooms),
            'routes_reaching_act2':act2,'routes_reaching_act3':act3,
            'scheduled_by_kind':dict(scheduled),'prefix_cache_hits':sum(r.get('checkpoint_prefix',0)>0 for r in ev.records),
            'pending_repairs':len(repairs),'executed_deep_boss_repairs':sum(bool((r.get('repair')or{}).get('deep'))for r in ev.records),
            'prefix_cache_misses':sum(not r.get('cache_hit') and r.get('checkpoint_prefix',0)==0 for r in ev.records)}
        if config.root_policies:metrics['evaluations_by_root_policy']={name:sum((r.get('family')or NATIVE)==name for r in ev.records if r.get('kind')!='gate_probe')for name in portfolio}
        if probing:
            batches=[r for r in ev.records if r.get('kind')=='gate_probe']
            metrics['gate_probes']={'batches':len(batches),'probes':sum(r.get('probes',0)for r in batches),'usable':sum(r.get('usable_probes',0)for r in batches),
                'tables_scheduled':{str(list(k)):v for k,v in sorted(gate_tables.items())},'search_seconds':sum(r.get('probe_search_seconds',0)for r in batches),
                'scope':'synthetic; allocation only, never a trajectory, checkpoint, cached result, bound or proof'}
        # The append-only ledger keeps every record. Live snapshots have bounded
        # record history; completion publishes the full report exactly once.
        live_tail=config.low_io and not force and len(ev.records)>64
        result.update(evaluations=ev.records[-64:]if live_tail else ev.records,
            evaluations_total=len(ev.records),evaluations_truncated=live_tail,evaluations_ledger='evaluations.jsonl',
            frontier=frontier.snapshot(),failures=failures[-64:]if live_tail else failures,failures_total=len(failures),
            lookahead_groups=groups_report[-64:]if live_tail else groups_report,lookahead_groups_total=len(groups_report),
            search_metrics=metrics,cache_hits=ev.cache.hits,
            checkpoints=ev.checkpoints.snapshot(limit=64 if live_tail else None) if ev.checkpoints else {},pool_stats=pool.stats,
            elapsed_seconds=perf_counter()-ev.started,best_label=best_label,
            best_observation=(best or {}).get('observation'),
            unresolved_regions=[{'kind':'all runs not excluded by a proof','upper_bound':1}])
        if models is not None:
            if force:models.refit()
            result['gate_models']=models.snapshot()
        if certificate:result.update(status='VERIFIED_WIN_IN_NATIVE_HOST',lower_bound=1,optimal_in_backend=True,unresolved_regions=[])
        write_json(out/'result.json',result,compact=config.low_io)

    def run(specs):
        scheduled.update(s['kind'] for s in specs)
        items=ev.batch(specs);absorb(items);save();return items

    if config.root_portfolio>1:
        if initial_prefix:raise ContractError('root portfolio only supports fresh initial runs')
        request=ev.request([])
        request['stop_at_strategic_decision']=True
        # A menu-only observation must not outrank dead full campaigns merely
        # because it is alive at floor zero; do not insert it into the frontier.
        scheduled['root_menu']+=1
        initial=ev.batch([{'kind':'root_menu','category':'exploration','request':request}])
        roots,deferred=root_requests(initial[0][1],ev,min(config.root_portfolio,lanes)) if initial else ([],0)
        result['root_portfolio']={'offered_branches_scheduled':len(roots),'deferred_actions':deferred,
            'source':'actual native initial menu','prior_routes_loaded':False}
        if roots:urgent.extend(roots)
        else:run([{'kind':'baseline','category':'exploration','request':ev.request([])}])
    else:
        # The baseline alone leaves every other worker idle for its whole
        # duration: the extra root policies cost no wall time.
        run([{'kind':'baseline','category':'exploration','request':ev.request(initial_prefix or [])}]+
            [{'kind':'root_policy','category':'exploration','family':name,'request':ev.request([])}for name in config.root_policies])
        if config.root_policies:result['root_policies']={'portfolio':list(portfolio),'continuation':'lineage keeps its root policy table',
            'tables_are':'diversity sources; allocation only, never legality or a bound'}
    while not certificate:
        can_schedule=ev.serial<config.evaluations and perf_counter()<ev.deadline
        window=config.dispatch_window if config.dispatch_mode=='ordered'else lanes
        free=max(0,window-ev.pending_count)
        specs=[]
        if can_schedule:
            for slot in range(min(free,config.evaluations-ev.serial)):
                if urgent:
                    specs.append(urgent.popleft());continue
                probes=(scheduled['combat_probe']+scheduled['gate_retry']
                        +sum(s['kind'] in ('combat_probe','gate_retry') for s in specs))
                if repairs and (probes==0 or probes<.2*(sum(scheduled.values())+len(specs)+1)):
                    specs.append(repairs.popleft());continue
                group=scheduler.next()
                if group:
                    group['prefix_length']=len(group['prefix'])
                    request=ev.request(group.pop('prefix'),policy=group.get('policy',0),
                        horizon=None if config.scheduler=='dfs' else group['floor']+config.lookahead_floors,
                        actions=config.max_decisions if config.scheduler=='dfs' else
                            min(config.max_decisions,group['prefix_length']+config.lookahead_actions))
                    specs.append({'kind':'macro_'+group['category'],'category':group['category'],
                                  'group':group,'family':families.get(group.get('source')),'request':request})
                    groups_report.append(group)
                else:
                    # Restarts rotate through the root policies (native only without a portfolio).
                    specs.append({'kind':'restart','category':'exploration','family':portfolio[restarts%len(portfolio)]if restarts%len(portfolio)else None,
                                  'request':ev.request(scope_prefix or [],policy=ev.serial+slot+1)})
                    restarts+=1
        if specs:
            scheduled.update(s['kind'] for s in specs);ev.dispatch(specs)
        if not ev.pending_count:break
        trials=ev.collect(ordered=True) if config.dispatch_mode=='ordered'else ev.collect()
        if config.dispatch_mode=='batch':
            while ev.pending_count:trials.extend(ev.collect())
            trials.sort(key=lambda item:int(item[2].split('-')[1]))
        if not trials:continue
        absorb(trials);save()
        if certificate:
            ev.cancel_remaining();save();break
        viable=[t for t in trials if t[0]['kind'].startswith('macro_') and
                classify_failure(t[1]) in ('SEARCH_BUDGET','DECISION_BOUNDARY')]
        for spec,candidate,label in sorted(viable,key=lambda x:utility(x[1]),reverse=True)[:config.survivors]:
            prefix=candidate.get('trace',[]);key=canonical(prefix)
            if prefix and len(prefix)<config.max_decisions and key not in seen_rollouts:
                seen_rollouts.add(key);urgent.append({'kind':'deepen','category':'exploration','family':families.get(label),'request':ev.request(prefix)})
    result['stop_reason']='verified_boolean_maximum' if certificate else ('time_budget' if perf_counter()>=ev.deadline else 'evaluation_budget')
    save(force=True);return result
