"""Experimental real-menu paired continuations; allocation only, never proofs.

Every arm selects an actually offered native card instance or the native skip.
The intervening rooms execute normally; only combat streams at the final F1
map boundary are sampled in disposable workers. A complete table may propose
a separate, unsampled real rollout of the preferred legal menu action.
"""
from __future__ import annotations
from copy import deepcopy
from collections import Counter
import hashlib
import math
from ..canonical import canonical
from .archive import combat_loss_progress
from .paired_card_probes import SAMPLES, MAP_OBSERVATION_KEYS, probe_workload

SCOPE = 'synthetic paired third-act menu continuations; exact menu-bound allocation only; never a route, cache, checkpoint, cut or bound'


def valid_menu_outcome(outcome):
    if not isinstance(outcome,dict) or type(outcome.get('won')) is not bool or type(outcome.get('stage')) is not int:
        return False
    stage, hp, loss = outcome['stage'], outcome.get('hp_fraction'), outcome.get('lost')
    if type(hp) not in (int,float) or not math.isfinite(hp) or hp < 0:
        return False
    if outcome['won']:
        return stage == 2 and hp > 0 and loss is None
    if stage not in (0,1) or hp != 0:
        return False
    return loss is None or (isinstance(loss,(list,tuple)) and len(loss)==2
        and all(type(v) in (int,float) and math.isfinite(v) for v in loss)
        and 0 <= loss[0] <= loss[1] and loss[1] > 0)


def menu_probe_rejection(result, request, error=None):
    if error:
        return str(error)
    if not isinstance(result,dict):
        return 'MISSING_NATIVE_REPORT'
    if result.get('reason'):
        return str(result['reason'])
    return 'OUTCOME_CONTRACT_REJECTED'


def menu_probe_outcome(result, request):
    """Reject partial, unbound or failed worker results. An observed native
    route death is a measured loss; resource/host failures remain UNKNOWN."""
    if (not isinstance(result, dict) or result.get('schema') != 'spire-native-card-menu-probe/v1'
            or result.get('status') != 'CARD_MENU_PROBE' or result.get('synthetic') is not True
            or result.get('reason') is not None or result.get('value') is not None
            or result.get('native_terminal_observed') is not False):
        return None
    if not isinstance(request,dict):return None
    info, spec = result.get('card_menu_probe'), request.get('card_menu_probe')
    if not isinstance(info,dict) or not isinstance(spec,dict):return None
    history = request.get('history') or []
    if (not isinstance(history,list) or not isinstance(spec.get('choice'),dict)
            or not isinstance(spec.get('expected_actions'),list) or not spec['expected_actions']
            or not isinstance(spec.get('expected_entry_observation'),dict)
            or not isinstance(spec.get('expected_source_native_state'),dict)
            or type(spec.get('rng')) is not int or not 0<=spec['rng']<(1<<64)):
        return None
    if (info.get('menu_checked') is not True or info.get('native_finished') is not True
            or type(result.get('consumed')) is not int or result['consumed'] != len(history)
            or type(info.get('choice_index')) is not int or info['choice_index'] != len(history)
            or type(info.get('native_won')) is not bool or type(info.get('rng')) is not int or info['rng'] != spec['rng']):
        return None
    try:
        if spec['choice'].get('kind') not in ('card_reward','card_skip') or not any(canonical(a)==canonical(spec['choice'])for a in spec['expected_actions']):return None
        for actual, wanted in [('choice','choice'), ('source_observation','expected_entry_observation'), ('source_actions','expected_actions'), ('source_native_state','expected_source_native_state')]:
            if canonical(info.get(actual)) != canonical(spec.get(wanted)):
                return None
        evidence = result.get('decision_evidence') or []
        trace = result.get('trace') or []
        if not isinstance(trace,list) or not isinstance(evidence,list) or len(trace) <= len(history) or len(evidence) != len(trace):
            return None
        if canonical(trace[:len(history)]) != canonical(history) or canonical(trace[len(history)]) != canonical(spec['choice']):
            return None
        obs = result.get('observation') or {}
        if not isinstance(obs,dict):return None
        if info['native_won']:
            if (info.get('sample_applied') is not True or info.get('f1_won') is not True
                    or info.get('f2_won') is not True or not isinstance(info.get('f2_entry'),dict)):
                return None
            maximum = float(info['f2_entry'].get('max_hp') or 0)
            hp = float(obs.get('hp') or 0)
            if not math.isfinite(hp) or not math.isfinite(maximum) or maximum <= 0 or hp <= 0:
                return None
            return {'won':True,'stage':2,'hp_fraction':hp/maximum,'lost':None}
        if float(obs.get('hp') or 0) != 0:
            return None
        first, second = info.get('f1_entry'), info.get('f2_entry')
        stage = 1 if info.get('f1_won') is True else 0
        entry = second if stage == 1 else first
        if entry is None:
            # A legal continuation died before that gate. No invisible fight
            # is invented; its preparation outcome is zero for this stage.
            return {'won':False,'stage':stage,'hp_fraction':0.0,'lost':None}
        if (not isinstance(entry,dict) or info.get('sample_applied') is not True
                or (obs.get('act'),obs.get('floor')) != (entry.get('act'),entry.get('floor'))):
            return None
        progress = combat_loss_progress(result)
        if not progress.get('available') or not progress.get('terminal_snapshot_available'):
            return None
        removed, total = progress['observed_hp_removed'], progress['observed_life_hp']
        if not all(math.isfinite(v) for v in (removed,total)) or not 0 <= removed <= total or total <= 0:
            return None
        return {'won':False,'stage':stage,'hp_fraction':0.0,'lost':[removed,total]}
    except (KeyError,TypeError,ValueError,OverflowError):
        return None


def score(outcome, totals):
    if outcome['won']:
        return 2.0 + outcome['hp_fraction']
    loss = outcome['lost']
    return float(outcome['stage']) + (min(1.0,loss[0]/totals[outcome['stage']]) if loss else 0.0)


class RealCardMenuProbes:
    def __init__(self, enabled=False, solver_seed=0, *, context=None, inputs=None, inactive_reason=None):
        self.enabled, self.solver_seed = enabled, solver_seed
        self.context, self.inputs = deepcopy(context), deepcopy(inputs)
        self.inactive_reason = 'disabled' if not enabled else inactive_reason
        self.seen, self.tables, self.next_threshold = set(), [], 1
        self.attempted, self.usable, self.errors, self.completed = 0, 0, 0, 0
        self.work = {'measured_expanded_combat_nodes':0,'measured_probe_search_seconds':0.0,
                     'measured_probe_native_seconds':0.0,'unknown_work_probes':0}
        self.real_proposals = 0
        self.rejection_reasons, self.source_issues = Counter(), Counter()

    def observe(self, result, label, searched, template, family=None):
        if not self.enabled or self.inactive_reason:
            return []
        if (template.get('character') != 'IRONCLAD' or template.get('ascension') != 10 or template.get('unlocks') != 'all'
                or not template.get('advisor') or type(template['advisor'].get('nodes')) is not int
                or template['advisor']['nodes'] < 1):
            self.inactive_reason = 'requires_ironclad_a10_all_with_count_budget_advisor'
            return []
        campaign = result.get('campaign') or {}
        if campaign.get('act_count') != 3 or campaign.get('final_act_boss_count') != 2:
            return []
        trace, evidence, specs = result.get('trace') or [], result.get('decision_evidence') or [], []
        sources = {s['index']:s for s in result.get('card_menu_sources') or []
                   if isinstance(s,dict) and type(s.get('index')) is int}
        for index in range(searched,min(len(trace),len(evidence))):
            row = evidence[index]
            obs, actions = row.get('observation') or {}, row.get('available_actions') or []
            if row.get('phase') != 'card_reward' or obs.get('act') != 2 or obs.get('turn') is not None:continue
            source = sources.get(index)or{}
            source_state = source.get('source_native_state')
            if (not MAP_OBSERVATION_KEYS <= set(obs) or not isinstance(obs.get('rng'),dict)
                    or not isinstance(source_state,dict) or set(source_state) != {'run','progress','action_runtime','reward_sync'}
                    or not all(isinstance(source_state[k],dict) for k in source_state)):
                self.source_issues[source.get('error') or 'SOURCE_GUARD_UNAVAILABLE'] += 1
                continue
            # Exact native actions retain menu index and the original offered
            # instance's acquisition path. Alternatives/rerolls are deferred.
            arms = [a for a in actions if isinstance(a,dict) and a.get('kind') == 'card_skip']
            arms += [a for a in actions if isinstance(a,dict) and a.get('kind') == 'card_reward']
            if not arms or arms[0].get('kind') != 'card_skip' or len(arms) < 2:
                continue
            try:
                key = canonical({'context':self.context,'inputs':self.inputs,'history':trace[:index],
                                 'native_context':{k:template.get(k)for k in ('seed','character','ascension','unlocks')},
                                 'observation':obs,'actions':actions,'source_native_state':source_state})
            except (TypeError,ValueError):
                # A native serializer value outside the exact request contract
                # is an unsupported identity, never a rounded substitute.
                self.source_issues['SOURCE_OUTSIDE_EXACT_JSON_CONTRACT'] += 1;continue
            if key in self.seen:
                continue
            self.seen.add(key)
            if len(self.seen) < self.next_threshold:
                continue
            number = len(self.tables)
            samples = [int.from_bytes(hashlib.sha256(f'real-card-menu/v1:{self.solver_seed}:{number}:{i}'.encode()).digest()[:8],'big')
                       for i in range(SAMPLES)]
            table = {'table':number,'source':label,'family':family,'index':index,'history':deepcopy(trace[:index]),
                     'observation':deepcopy(obs),'actions':deepcopy(actions),'arms':deepcopy(arms),'chosen':deepcopy(trace[index]),
                     'source_native_state':deepcopy(source_state),
                     'template':deepcopy(template),'samples':samples,'rows':{},'status':'UNKNOWN','complete_paired_samples':0,
                     'means':[],'gains':[],'best_action':None,'threshold':self.next_threshold}
            self.tables.append(table)
            self.next_threshold = 32 if number == 0 else self.next_threshold*4
            for sample in samples:
                requests = []
                for action in arms:
                    request = deepcopy(template)
                    for field in ('checkpoint','probe','f1_winner_proposal','capture_f1_winners','capture_card_menu_state','real_card_menu_choice','expected_evidence','stop_at_floor','stop_at_strategic_decision','map_route_plan','capture_route_graph','capture_resource_telemetry','preserve_completed_prefix'):
                        request.pop(field,None)
                    request.update(history=deepcopy(table['history']),capture_checkpoints=False,generate_candidate=True,
                        card_menu_probe={'expected_entry_observation':deepcopy(obs),'expected_actions':deepcopy(actions),
                                         'expected_source_native_state':deepcopy(source_state),
                                         'choice':deepcopy(action),'rng':sample})
                    requests.append(request)
                specs.append({'kind':'real_card_menu_probe','category':'probe','gate':(2,'card_menu'),'table':number,
                    'sample':sample,'base':label,'labels':[canonical(a).decode('utf-8') for a in arms],
                    'requests':requests,'request':{'history':deepcopy(table['history']),'generate_candidate':False}})
        return specs

    def absorb(self, spec, outcomes, workload=None, rejections=None):
        if not isinstance(outcomes,(list,tuple)):outcomes=[]
        table = self.tables[spec['table']]
        if spec['sample'] not in table['samples'] or spec['sample'] in table['rows']:
            raise ValueError('unexpected/duplicate real menu sample')
        if spec['labels'] != [canonical(a).decode('utf-8') for a in table['arms']]:
            raise ValueError('real menu arms changed')
        self.completed += 1
        self.attempted += len(table['arms'])
        self.usable += sum(valid_menu_outcome(o) for o in outcomes)
        self.errors += sum(not valid_menu_outcome(o) for o in outcomes) + max(0,len(table['arms'])-len(outcomes))
        for index in range(max(len(outcomes),len(table['arms']))):
            if index>=len(outcomes)or not valid_menu_outcome(outcomes[index]):
                self.rejection_reasons[(rejections[index] if rejections and index<len(rejections) else None)or'MISSING_OR_INVALID_OUTCOME']+=1
        if workload is None:
            workload = {'measured_expanded_combat_nodes':0,'measured_probe_search_seconds':0.0,
                        'measured_probe_native_seconds':0.0,'unknown_work_probes':len(table['arms'])}
        for key in self.work:
            self.work[key] += workload[key]
        complete = len(outcomes) == len(table['arms']) and all(valid_menu_outcome(o) for o in outcomes)
        table['rows'][spec['sample']] = deepcopy(outcomes) if complete else None
        table['complete_paired_samples'] = sum(r is not None for r in table['rows'].values())
        if table['complete_paired_samples'] != SAMPLES:
            return []
        rows = [table['rows'][s] for s in table['samples']]
        totals = {stage:max([o['lost'][1] for group in rows for o in group if o['stage']==stage and o['lost']] or [1.0])
                  for stage in (0,1)}
        means = [sum(score(group[i],totals) for group in rows)/SAMPLES for i in range(len(table['arms']))]
        best = max(range(len(means)),key=lambda i:(means[i],-i))
        table.update(status='PAIRED_SIGNAL',means=means,gains=[m-means[0] for m in means],best_action=deepcopy(table['arms'][best]))
        if canonical(table['best_action']) == canonical(table['chosen']):
            return []
        request = deepcopy(table['template'])
        for field in ('probe','card_menu_probe','checkpoint','f1_winner_proposal','expected_evidence','stop_at_floor','stop_at_strategic_decision'):
            request.pop(field,None)
        request.update(history=deepcopy(table['history']),generate_candidate=True,
            capture_checkpoints=False,
            real_card_menu_choice={'expected_entry_observation':deepcopy(table['observation']),
                                  'expected_source_native_state':deepcopy(table['source_native_state']),
                                  'expected_actions':deepcopy(table['actions']),'choice':deepcopy(table['best_action'])})
        self.real_proposals += 1
        return [{'kind':'real_card_menu_followup','category':'preparation','family':table['family'],
                 'source':table['source'],'menu_probe_table':table['table'],'request':request}]

    def snapshot(self):
        public_tables = [{k:v for k,v in t.items() if k not in ('rows','history','template','observation','actions','source_native_state')}
                         for t in self.tables]
        return {'enabled':self.enabled,'inactive_reason':self.inactive_reason,'scope':SCOPE,
                'samples_per_arm':SAMPLES,'distinct_menus':len(self.seen),'next_entry_threshold':self.next_threshold,
                'completed_batches':self.completed,'attempted_probes':self.attempted,'usable_probes':self.usable,
                'errors_or_missing':self.errors,'real_rollouts_proposed':self.real_proposals,
                'source_guard_issues':dict(self.source_issues),'rejection_reasons':dict(self.rejection_reasons),
                'work_measurement_complete':self.work['unknown_work_probes']==0,**self.work,'tables':public_tables}
