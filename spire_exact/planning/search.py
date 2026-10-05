"""Failure-directed macro search with native short-horizon action comparisons.

Not an exact enumerator: capped actions, archive eviction, rollout utility and
beam budgets are heuristic. All unexcluded runs retain Boolean upper bound 1.
"""
from __future__ import annotations
from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from ..pausable_clock import active_counter as perf_counter, paused_seconds
import json,os
import hashlib,time
from ..canonical import canonical, ContractError
from ..mode1 import OBJECTIVE,check_winning_replay,is_winning_candidate
from .io import read_json,write_json
from .archive import (CheckpointArchive,ResultCache,DiverseFrontier,utility,
                      decision_groups,classify_failure,failure_combat_prefix,combat_loss_progress)
from .pool import NativePool,NativeWorker,WorkerError
from .resources import ResourcePlan
from .runtime_metrics import snapshot as runtime_snapshot
from .policies import CARDS,NATIVE,POLICIES,jitter as jitter_tiers,merge as merge_tiers,resolve as resolve_policies
from .probes import gate_entries,probe_request,probe_outcome,edit_candidates,offered_cards
from .paired_card_probes import PairedCardProbes,paired_outcome,probe_workload,valid_outcome
from .real_card_menu_probes import RealCardMenuProbes,menu_probe_outcome,menu_probe_rejection
from .f1_winners import F1WinnerReuse
from .preparation import PreparationCandidates,enabled as preparation_enabled,captures_graph,ROUTE_KINDS
from .final_defaults import FINAL_FEATURES,I081_FEATURES,F2_READINESS_EVERY_DEFAULT
from .f2_readiness import F2Readiness,readiness_outcome
from .f2_joint_model import F2JointModel
from .macro_i085 import MacroService, MacroRoutePortfolio
from .aux_governor import AuxGovernor

SYNTHETIC_KINDS=('gate_probe','paired_card_probe','real_card_menu_probe','f2_readiness_probe')


def dispatch_blocked(spec,scheduler):
    blocked=getattr(scheduler,'dispatch_blocked',lambda _:False)(spec)
    if spec.get('kind') in (*ROUTE_KINDS,'macro_forge'):
        blocked=blocked or getattr(scheduler,'preparation_blocked',lambda _:False)(spec)
    return blocked


def take_dispatchable(queue,scheduler,allow=None):
    """Defer blocked work without changing the order of any retained item.
    `allow` (i100 governor) may defer more items; None = legacy."""
    for index,spec in enumerate(queue):
        if not dispatch_blocked(spec,scheduler) and (allow is None or allow(spec)):
            del queue[index]
            return spec
    return None


def research_consumer(spec):
    request=spec.get('request')or{}
    return spec.get('kind')in('f1_winner_reuse','real_card_menu_followup','memory_prefix_followup',*ROUTE_KINDS)or bool(request.get('f1_winner_proposal')or request.get('real_card_menu_choice')or request.get('map_route_plan'))


def research_role(config,spec):
    if not(config.f1_winner_reuse or config.real_card_menu_probes or preparation_enabled(config)or config.memory_telemetry or config.preserve_completed_prefix):return None
    request=spec.get('request')or{}
    if spec['kind']=='real_card_menu_probe':return 'synthetic_B'
    if spec['kind']=='f1_winner_reuse'or request.get('f1_winner_proposal'):return 'consumer_A'
    if spec['kind']=='real_card_menu_followup'or request.get('real_card_menu_choice'):return 'consumer_B'
    if request.get('map_route_plan'):return 'consumer_preparation'
    if spec['kind']=='memory_prefix_followup':return 'consumer_memory_prefix'
    if spec['kind']=='macro_forge':return 'preparation_menu'
    a,b=bool(request.get('capture_f1_winners')),bool(request.get('capture_card_menu_state'))
    role='source_AB' if a and b else 'source_A' if a else 'source_B' if b else None
    if request.get('capture_route_graph')or request.get('capture_resource_telemetry'):
        return (role+'_preparation')if role else 'source_preparation'
    return role or('source_memory'if request.get('memory_telemetry')or request.get('preserve_completed_prefix')else None)


def measured_research_work(result,*,cache_hit=False,submit_to_absorb_seconds=None):
    """Read reported costs; absent native metrics are unknown, not zero. The
    coordinator interval includes queue/startup/ordered absorption waits and
    is not presented as native execution time or a measured speedup."""
    names=('native_wall_us','native_cpu_us','expanded_combat_nodes','beam_search_us',
           'prefix_native_execute_us','prefix_replayed_actions','checkpoint_skipped_actions','new_actions')
    metrics={name:0 if cache_hit else None for name in names}
    if not cache_hit:
        perf=(result or {}).get('performance')or{}
        counts=perf.get('counters')or{};stages=perf.get('exclusive_stages')or{}
        for name,owner,key in [('native_wall_us',perf,'wall_us'),('native_cpu_us',perf,'cpu_us'),
                ('prefix_replayed_actions',counts,'prefix_replayed_actions'),('checkpoint_skipped_actions',counts,'checkpoint_skipped_actions'),('new_actions',counts,'new_actions')]:
            value=owner.get(key)
            if type(value)is int and value>=0:metrics[name]=value
        for name,key in [('beam_search_us','beam_search'),('prefix_native_execute_us','prefix_native_execute')]:
            value=(stages.get(key)or{}).get('us')
            if type(value)is int and value>=0:metrics[name]=value
        searches=((result or {}).get('advisor_metrics')or{}).get('searches')
        if isinstance(searches,list)and all(isinstance(s,dict)and type(s.get('expanded_nodes'))is int and s['expanded_nodes']>=0 for s in searches):
            metrics['expanded_combat_nodes']=sum(s['expanded_nodes']for s in searches)
    return {'cache_hit':cache_hit,'native_execution_occurred':False if cache_hit else True if metrics['native_wall_us']is not None else None,'metrics':metrics,
            'submit_to_absorb_seconds':submit_to_absorb_seconds,'missing_metrics':[k for k,v in metrics.items()if v is None],
            'scope':'native reported work; submit-to-absorb includes queue/startup/ordered waiting; not a throughput comparison'}


def research_cost_summary(records):
    groups={}
    for record in records:
        work=record.get('research_work')
        if not isinstance(work,dict):continue
        role=work['role'];group=groups.setdefault(role,{'evaluations':0,'cache_hits':0,'checkpoint_restores':0,
            'isolated_consumers':0,'submit_to_absorb_seconds_measured':0.0,'submit_interval_unknown':0,'metrics':{},'reasons':{}})
        group['evaluations']+=1;group['cache_hits']+=bool(work.get('cache_hit'))
        group['checkpoint_restores']+=int(record.get('checkpoint_prefix',0)>0)
        group['isolated_consumers']+=bool(work.get('isolated'))
        interval=work.get('submit_to_absorb_seconds')
        if interval is None:group['submit_interval_unknown']+=1
        else:group['submit_to_absorb_seconds_measured']+=interval
        for name,value in work['metrics'].items():
            metric=group['metrics'].setdefault(name,{'measured_sum':0,'unknown_evaluations':0})
            if value is None:
                metric['unknown_evaluations']+=1
                subset=(work.get('measured_subsets')or{}).get(name)
                if type(subset) in (int,float)and subset>=0:metric['measured_sum']+=subset
            else:metric['measured_sum']+=value
        if record.get('reason'):
            reason=str(record['reason']);group['reasons'][reason]=group['reasons'].get(reason,0)+1
    return {'groups':groups,'scope':'recorded costs only; no runtime measurement or throughput conclusion is inferred from absent reports'}

@dataclass(frozen=True)
class SearchConfig:
    evaluations: int=24
    seconds: int=600
    task_seconds: int=90
    max_decisions: int=2000
    lookahead_actions: int=80
    lookahead_floors: int=2
    alternatives: int=6
    survivors: int=2
    max_repair_window: int=32
    archive_entries: int=48
    checkpoint_mib: int=512
    cache_mib: int=64
    scheduler: str='weighted'
    dispatch_mode: str='stream'
    repair_mode: str='fifo'
    boss_repair_ms: int=30000
    root_portfolio: int=1
    solver_seed: int=0
    low_io: bool=False
    dispatch_window: int=56
    snapshot_stride: int=7
    event_driven_settle: bool=False
    # focus scheduler: trajectories sharing the focus share, trajectories kept
    # with full decision sites, focus evaluations per explorer evaluation, and
    # the explorer's per-menu deferral for very wide menus.
    focus_elites: int=8
    focus_pool: int=48
    focus_share: int=3
    site_cap: int=12
    # Send the per-seed gate-model tiers with every generated rollout.
    prior: bool=False
    # repair_mode 'gate': advisor patches tried in order on a fatal fight entry,
    # best trajectory first. Each patch is plain request data.
    gate_retry_plans: tuple=()
    # A fatal fight is retried only when it was close: a life of an enemy was
    # already removed, or at least this percentage of the current enemy HP.
    # Heavier plans rescued 13 of 15 rescuable retained boss entries at 50 and
    # none of the fights below it (iteration-037 component evidence).
    gate_retry_percent: int=50
    # Extra root rollout policies (policies.POLICIES, besides 'native'): each
    # gets one fresh rollout next to the baseline while the other workers are
    # idle, and every evaluation derived from a trajectory keeps that
    # trajectory's policy table. Diversity of deck families, allocation only.
    root_policies: tuple=()
    # focus scheduler: at most this many elites whose final decks are alike
    # (0 = no limit, the historical behaviour), and the multiset-Jaccard
    # percentage from which two decks count as alike.
    focus_cluster_cap: int=0
    focus_cluster_percent: int=75
    # focus scheduler, iteration-051 (both off by default). focus_family: an
    # elite's share is its rank share times the optimistic pass rate of its
    # deck family at the gate it still has to pass (GateModels.family: every
    # real entry with an alike deck, normalised by attempts). focus_optimism:
    # percentage of one standard error added to an option's value for items the
    # gate model has seen little of (GateModels.novelty). Allocation only.
    focus_family: bool=False
    focus_optimism: int=0
    # focus scheduler, iteration-054 (off by default): elite places alternate
    # between the pool order and the same order with deaths in a later boss of
    # an act ranked by the HP carried into that fight. Allocation only.
    focus_carry: bool=False
    # focus scheduler, iteration-057 (0 = off): from this many distinct entries
    # without a pass, the furthest boss gate counts as stalled when it is the
    # first boss of a later act; focus picks of the trajectories that died in
    # its act then alternate with the act before. A count of attempts, not
    # time or nodes. Allocation only.
    focus_stall: int=0
    focus_stall_extended: bool=False
    focus_stall_f2: bool=False
    # iteration-058 (both off by default; worker time, allocation unchanged).
    # root_async: the root batch (baseline and root policies) is no barrier:
    # the first round is root_round evaluations (at least the root batch),
    # restarts after the root rollouts, and nothing else is dispatched until
    # the root results are absorbed. iteration-069: the round no longer follows
    # the number of workers, which changed the path; 7 is the round the
    # default 7 workers had, so those runs keep their paths.
    # requeue_lost: an evaluation lost to memory (admission denied: it never
    # ran; worker memory budget: stopped, requeued with half the combat node
    # budgets) is dispatched again, at most this many times per proposal.
    root_async: bool=False
    root_round: int=7
    requeue_lost: int=0
    # iteration-061 (off by default): jittered policy tables (policies.jitter),
    # the lineage's table with about jitter_percent of the card labels moved by
    # one or two tiers, reproducible from solver_seed and the table's tag; every
    # evaluation of that lineage keeps it. restart_jitter: each restart gets one
    # (policy_seed alone almost never changes a rollout, so restarts repeated
    # the root rollouts; iteration-058 results, section 6). root_jitter: this
    # many fresh rollouts with one each, base tables rotating through native and
    # the root policies, first in line after the root batch (in its idle lanes
    # with root_async); the root barrier does not wait for them.
    # Allocation only.
    restart_jitter: bool=False
    root_jitter: int=0
    jitter_percent: int=20
    # Synthetic gate probes (probes.py): when a boss has this many distinct real
    # entries, one table of one-card edits (add the cards this seed offers most,
    # remove / upgrade each card of the deck) is probed from the lost entry that
    # came closest; one table per number, () = off. The probes run in disposable
    # workers, never become trajectories and only feed the gate model's values.
    gate_probe_schedule: tuple=()
    gate_probe_cards: int=40
    gate_probe_chunk: int=8
    # Advisor patch for the probe fights; None keeps the run's own gate plan.
    gate_probe_plan: dict|None=None
    # iteration-068 production integration: five shared combat RNG samples per
    # base/add-card arm at real third-act F1 entries. Synthetic allocation only.
    paired_card_probes: bool=True
    # i082, all off here (the i082 entry turns them on): tables every N distinct
    # F1 entries (0 = 1, 32, 128, 512), skip a table with the same deck/relics/
    # candidates, mean over complete tables, add-card arms as joint-model rows.
    paired_card_every: int=0
    paired_card_dedup: bool=False
    paired_card_merge: str='latest'
    paired_card_joint: bool=False
    # i081 defaults belong to the public preset; old low-level callers stay off.
    f2_readiness_probes: bool=False
    f2_dead_retry: bool=False
    f2_joint_focus: bool=False
    f2_joint_model: bool=False
    f2_readiness_every: int=F2_READINESS_EVERY_DEFAULT
    # Independent research switches, both off. i070's existing signal remains
    # on by default; neither switch changes native requests when disabled.
    f1_winner_reuse: bool=False
    real_card_menu_probes: bool=False
    # i075: concrete route/menu candidates and measurement, all off. None of
    # these thresholds or per-trajectory limits are pruning certificates.
    gold_shop_routes: bool=False
    low_hp_routes: bool=False
    low_hp_routes_any_act: bool=False
    lean_third_act: bool=False
    shop_preparation: bool=False
    resource_telemetry: bool=False
    gold_shop_threshold: int=200
    low_hp_percent: int=50
    preparation_limit: int=4
    forge_menu_dedup: str='exact'
    memory_telemetry: bool=False
    preserve_completed_prefix: bool=False
    prefer_f1_hp: bool=False
    # i085 is an independent macro-only candidate. Direct/legacy calls stay off.
    gate_timing: bool=False
    tail_mode: str="off"
    tail_sites: int=1024
    tail_mib: int=16
    tail_cohort_inflight: int=2
    tail_feedback_rounds: int=2
    gate_model_minimum: int=24
    gate_model_refresh: int=16
    macro_plateau: bool=False
    macro_widening: bool=False
    macro_fair: bool=False
    macro_routes: str='off'
    macro_aux_burst: int=4
    macro_route_limit: int=3
    macro_route_every: int=8
    macro_route_paths: int=256
    macro_route_expansions: int=4096
    macro_route_queue_mib: int=16
    # i100 (all off here; the i100 entry turns them on): gate
    # clinic root-versus-depth diagnosis steering focus picks, verdict-aware
    # gate-retry share/order, auxiliary-work governor, structural strategy
    # prior and a later first paired table. Allocation only.
    clinic: bool=False
    clinic_min_entries: int=6
    clinic_root_share: int=85
    clinic_undecided_share: int=34
    clinic_depth_share: int=10
    clinic_retry_share: int=35
    clinic_aux_share: int=10
    clinic_aux_contended: int=30
    clinic_hints: bool=False
    strategy_prior: str='off'
    paired_card_first: int=1
    @classmethod
    def final(cls,**options):
        """Production entry preset; plain constructor remains a legacy adapter."""
        return cls(**(FINAL_FEATURES|I081_FEATURES|options))
    def __post_init__(self):
        if type(self.gate_timing) is not bool:raise ValueError('gate_timing must be boolean')
        if self.tail_mode not in ('off','shadow','on'):raise ValueError('invalid tail_mode')
        if self.tail_mode!='off' and self.scheduler!='focus':raise ValueError('tail search requires focus')
        for name in ('macro_plateau','macro_widening','macro_fair'):
            if type(getattr(self,name)) is not bool:raise ValueError(name+' must be boolean')
        if self.macro_routes not in ('off','shadow','on'):raise ValueError('invalid macro_routes mode')
        if (self.macro_plateau or self.macro_widening or self.macro_fair) and self.scheduler!='focus':
            raise ValueError('i085 macro allocation switches require the focus scheduler')
        if type(self.clinic) is not bool:raise ValueError('clinic must be boolean')
        if type(self.clinic_hints) is not bool:raise ValueError('clinic_hints must be boolean')
        if self.clinic_hints and not (self.clinic and self.strategy_prior!='off'):
            raise ValueError('clinic_hints need the clinic and a strategy prior')
        if self.clinic and (self.scheduler!='focus' or self.tail_mode!='off' or self.focus_stall):
            raise ValueError('the gate clinic needs the focus scheduler, tail_mode off and no count-based focus stall')
        if type(self.clinic_min_entries) is not int or self.clinic_min_entries<3:raise ValueError('clinic_min_entries must be >= 3')
        for name in ('clinic_root_share','clinic_undecided_share','clinic_depth_share','clinic_retry_share',
                     'clinic_aux_share','clinic_aux_contended'):
            if type(getattr(self,name)) is not int or not 0<=getattr(self,name)<=100:raise ValueError(name+' must be a percentage')
        if self.strategy_prior not in ('off','builtin'):raise ValueError("strategy_prior must be 'off' or 'builtin'")
        if self.strategy_prior!='off' and not self.clinic:raise ValueError('strategy_prior is read by the gate clinic')
        if type(self.paired_card_first) is not int or self.paired_card_first<1:raise ValueError('paired_card_first must be positive')
        for name in I081_FEATURES:
            if type(getattr(self,name))is not bool:raise ValueError(name+' must be a boolean')
        if any((self.f2_dead_retry,self.f2_joint_focus,self.f2_joint_model))and not self.f2_readiness_probes:
            raise ValueError('F2 allocation switches require f2_readiness_probes')
        if self.f2_joint_focus and self.focus_carry:
            raise ValueError('f2_joint_focus and focus_carry cannot be combined')
        if type(self.paired_card_every) is not int or self.paired_card_every<0:raise ValueError('paired_card_every must be a non-negative integer')
        if type(self.paired_card_dedup) is not bool or type(self.paired_card_joint) is not bool:raise ValueError('paired card switches must be booleans')
        if self.paired_card_merge not in ('latest','mean'):raise ValueError("paired_card_merge must be 'latest' or 'mean'")
        if self.paired_card_joint and not(self.f2_joint_model and self.f2_readiness_probes and self.paired_card_probes):
            raise ValueError('paired_card_joint requires f2_joint_model, f2_readiness_probes and paired_card_probes')
        if any(type(v) is not int or v<1 for k,v in self.__dict__.items() if k not in I081_FEATURES and k not in ('scheduler','dispatch_mode','repair_mode','solver_seed','low_io','event_driven_settle','prior','gate_retry_plans','gate_retry_percent','root_policies','focus_cluster_cap','focus_cluster_percent','focus_family','focus_optimism','focus_carry','focus_stall','focus_stall_extended','focus_stall_f2','root_async','requeue_lost','restart_jitter','root_jitter','jitter_percent','gate_probe_schedule','gate_probe_plan','paired_card_probes','f1_winner_reuse','real_card_menu_probes','gold_shop_routes','low_hp_routes','low_hp_routes_any_act','lean_third_act','shop_preparation','resource_telemetry','low_hp_percent','memory_telemetry','preserve_completed_prefix','prefer_f1_hp','forge_menu_dedup','paired_card_every','paired_card_dedup','paired_card_merge','paired_card_joint','macro_plateau','macro_widening','macro_fair','macro_routes','tail_mode','gate_timing',
                'clinic','clinic_root_share','clinic_undecided_share','clinic_depth_share','clinic_retry_share','clinic_aux_share','clinic_aux_contended','clinic_hints','strategy_prior')):
            raise ValueError('all search budgets must be positive integers')
        schedule=self.gate_probe_schedule
        if type(schedule) not in (tuple,list) or any(type(n) is not int or n<1 for n in schedule) or list(schedule)!=sorted(set(schedule)):
            raise ValueError('gate probe schedule must be increasing positive entry counts')
        if self.gate_probe_plan is not None and type(self.gate_probe_plan) is not dict:raise ValueError('gate probe plan must be an advisor patch')
        if type(self.gate_retry_percent) is not int or not 0<=self.gate_retry_percent<=100:raise ValueError('gate retry percent must be an integer within 0..100')
        if type(self.root_policies) not in (tuple,list) or resolve_policies(self.root_policies)!=tuple(self.root_policies):
            raise ValueError('root policies must be distinct known policy names other than native')
        if type(self.focus_cluster_cap) is not int or self.focus_cluster_cap<0:raise ValueError('focus cluster cap must be a nonnegative integer')
        if type(self.focus_cluster_percent) is not int or not 1<=self.focus_cluster_percent<=100:raise ValueError('focus cluster percent must be an integer within 1..100')
        if type(self.focus_family) is not bool:raise ValueError('focus family must be a boolean')
        if type(self.focus_carry) is not bool:raise ValueError('focus carry must be a boolean')
        if type(self.focus_stall) is not int or self.focus_stall<0:raise ValueError('focus stall must be a nonnegative number of entries')
        if type(self.focus_stall_extended) is not bool:raise ValueError('extended focus stall must be a boolean')
        if self.focus_stall_extended and (self.scheduler!='focus' or self.focus_stall<=0):
            raise ValueError('extended focus stall requires focus scheduler and positive existing focus_stall')
        if type(self.focus_stall_f2)is not bool:raise ValueError('F2 focus stall must be a boolean')
        if self.focus_stall_f2 and not self.focus_stall_extended:
            raise ValueError('F2 focus stall requires extended focus stall')
        if self.forge_menu_dedup not in ('exact','threshold'):raise ValueError('forge menu dedup must be exact or threshold')
        if type(self.root_async) is not bool:raise ValueError('root async must be a boolean')
        if type(self.requeue_lost) is not int or self.requeue_lost<0:raise ValueError('requeue lost must be a nonnegative integer')
        if type(self.restart_jitter) is not bool:raise ValueError('restart jitter must be a boolean')
        if type(self.root_jitter) is not int or self.root_jitter<0:raise ValueError('root jitter must be a nonnegative number of rollouts')
        if type(self.jitter_percent) is not int or not 1<=self.jitter_percent<=100:raise ValueError('jitter percent must be an integer within 1..100')
        if type(self.focus_optimism) is not int or not 0<=self.focus_optimism<=400:raise ValueError('focus optimism must be an integer percentage within 0..400')
        if self.scheduler not in ('weighted','dfs','tree','incumbent','focus'):raise ValueError('unknown strategic scheduler')
        if self.dispatch_mode not in ('batch','stream','ordered'):raise ValueError('unknown dispatch mode')
        if self.repair_mode not in ('fifo','deep_boss','deep_final_boss','gate'):raise ValueError('unknown repair mode')
        if type(self.prior) is not bool:raise ValueError('prior must be a boolean')
        if type(self.paired_card_probes) is not bool:raise ValueError('paired card probes must be a boolean')
        if type(self.f1_winner_reuse) is not bool:raise ValueError('F1 winner reuse must be a boolean')
        if type(self.real_card_menu_probes) is not bool:raise ValueError('real card menu probes must be a boolean')
        for key in ('gold_shop_routes','low_hp_routes','low_hp_routes_any_act','lean_third_act','shop_preparation','resource_telemetry','memory_telemetry','preserve_completed_prefix','prefer_f1_hp'):
            if type(getattr(self,key))is not bool:raise ValueError(key+' must be a boolean')
        if type(self.low_hp_percent)is not int or not 1<=self.low_hp_percent<=100:raise ValueError('low HP percent must be within 1..100')
        if type(self.gate_retry_plans) not in (tuple,list) or any(type(p) is not dict for p in self.gate_retry_plans):
            raise ValueError('gate retry plans must be a sequence of advisor patches')
        if self.focus_pool<self.focus_elites:raise ValueError('focus pool must hold every elite')

def near_miss(result,percent):
    """Allocation signal only: was the fatal fight close enough to re-solve?"""
    progress=combat_loss_progress(result)
    if progress.get('revivals_observed',0)>0:return True
    fraction=progress.get('current_life_hp_removed_fraction')
    return bool(progress.get('available')) and fraction is not None and 100*fraction>=percent

# requeue_lost (iteration-058): failures that are about memory, not about the
# route. An admission denial never ran; it waits this long before it is
# dispatched again, so a memory shortage is not hit at once a second time.
LOST_REASONS=('MEMORY_ADMISSION_DENIED','NATIVE_TASK_MEMORY_BUDGET','NATIVE_TASK_OUT_OF_MEMORY')
REQUEUE_HOLD_SECONDS=10
# Fresh rollouts (empty history): a repeat of an earlier one's complete trace adds no information.
FRESH_KINDS=('baseline','root_policy','root_jitter','restart')

def halve_nodes(request):
    """Half of every combat node budget of a request: the advisor's and each gate plan member's."""
    advisor=request.get('advisor')or{}
    owners=[advisor]+[m for plan in (advisor.get('gate_plans')or{}).values() for m in plan.get('members',[])]
    for owner in owners:
        for key in ('nodes','normal_nodes'):
            if type(owner.get(key))is int:owner[key]=max(1,owner[key]//2)

def family_fields(spec):
    """Record fields of a spec's lineage: its root policy and, for a jittered table, the table's tag."""
    name,_,tag=(spec.get('family')or'').partition('~')
    return {**({'family':name}if name else{}),**({'jitter':tag}if tag else{})}

class Evaluator:
    # Defaults also apply to existing evaluator adapters which provide their own
    # constructor. Scoped diagnostics override them on the instance.
    scope_prefix=None
    stop_floor=None
    research_progress=None
    # Optional callable(serial) -> policy tiers, sampled when a request is dispatched.
    prior_provider=None
    def __init__(self,pool,ctx,directory,config,advisor=None,*,scope_prefix=None,stop_floor=None):
        self.scope_prefix=deepcopy(scope_prefix) if scope_prefix is not None else None
        self.stop_floor=stop_floor
        if stop_floor is not None and (type(stop_floor)is not int or stop_floor<1):raise ValueError('Positive diagnostic stop floor required')
        self.pool,self.ctx,self.directory,self.config,self.advisor=pool,ctx,directory,config,advisor
        self.cache=ResultCache(config.cache_mib*1024**2);self.checkpoints=None
        self.identity=None;self.serial=0;self.f2_probe_evaluations=0;self.records=[];self.started=perf_counter();self.wall_started=time.perf_counter()
        self.deadline=self.started+config.seconds
        self.inflight={};self.ready=[]
        self.research_started={}
        self.research_progress=None
        if (config.f1_winner_reuse or config.real_card_menu_probes or preparation_enabled(config)or config.preserve_completed_prefix)and advisor and scope_prefix is None and stop_floor is None:
            self.research_progress=pool.research_progress_baseline(ctx,directory/'research-progress-baseline',
                min(config.task_seconds,max(.1,self.deadline-perf_counter())))
    def load_archive(self,paths):
        for path in paths:
            try:
                node=read_json(path,resolve_checkpoint=False);identity=node['payload']['identity']
                if identity.get('host_sha256')!=self.pool.stamp['host_sha256']:continue
                if any(identity.get(k)!=self.pool.inputs['dependencies'][f] for k,f in
                    [('game_sha256','sts2.dll'),('godot_sha256','GodotSharp.dll'),('harmony_sha256','0Harmony.dll')]):continue
                if self.checkpoints is None:
                    self.identity=identity
                    self.checkpoints=CheckpointArchive(self.ctx,identity,self.config.checkpoint_mib*1024**2,
                        research_progress=self.research_progress)
                self.checkpoints.add(path)
            except (ValueError,KeyError,OSError):continue
    def request(self,prefix,*,policy=0,horizon=None,actions=None,advisor_scale=1,boss_budget_ms=None,advisor_patch=None):
        if self.scope_prefix is not None and canonical(prefix[:len(self.scope_prefix)])!=canonical(self.scope_prefix):
            raise ContractError('Request escaped the immutable diagnostic scope prefix')
        if self.stop_floor is not None:horizon=self.stop_floor if horizon is None else min(horizon,self.stop_floor)
        request={k:self.ctx[k] for k in ('seed','character','ascension','unlocks')}
        derived=policy if self.config.solver_seed==0 else int.from_bytes(hashlib.sha256(f'{self.config.solver_seed}:{policy}'.encode()).digest()[:4],'big')&0x7fffffff
        request.update(history=prefix,generate_candidate=True,policy_seed=derived,
            max_decisions=actions or self.config.max_decisions,capture_checkpoints=True,low_io=self.config.low_io)
        if self.config.event_driven_settle:request['event_driven_settle']=True
        if (self.config.paired_card_probes or self.config.real_card_menu_probes) and self.advisor and self.scope_prefix is None and self.stop_floor is None:
            request['include_campaign_metadata']=True
        if self.config.f1_winner_reuse and self.advisor and self.scope_prefix is None and self.stop_floor is None:
            request['capture_f1_winners']=True
        if self.config.real_card_menu_probes and self.advisor and self.scope_prefix is None and self.stop_floor is None:
            request['capture_card_menu_state']=True
        if captures_graph(self.config) and self.advisor and self.scope_prefix is None and self.stop_floor is None:
            request['capture_route_graph']=True
        if self.config.resource_telemetry and self.advisor and self.scope_prefix is None and self.stop_floor is None:
            request['capture_resource_telemetry']=True
        if self.config.lean_third_act and self.advisor and self.scope_prefix is None and self.stop_floor is None:
            request['capture_preparation_menus']=True
        if self.config.memory_telemetry:request['memory_telemetry']=True
        if self.config.preserve_completed_prefix:request['preserve_completed_prefix']=True
        if self.research_progress is not None:request['research_progress']=deepcopy(self.research_progress)
        if horizon is not None:request['stop_at_floor']=horizon
        if self.advisor:
            request['advisor']=deepcopy(self.advisor)
            if self.config.prefer_f1_hp:request['advisor']['prefer_f1_hp']=True
            # Native paired audit: prevent a second grant after thorns spent
            # this card's block. Explicit false remains available for controls.
            request['advisor'].setdefault('fix_consumed_block_compensation',True)
            if self.config.low_io:request['advisor'].setdefault('quiet_diagnostics',True)
            request['advisor']['budget_ms']*=advisor_scale
            request['advisor']['boss_budget_ms']*=advisor_scale
            # Count-budget runs must scale the actual search budget as well.
            # Previously A10 probes only raised an already nonbinding wall cap.
            if request['advisor'].get('nodes')is not None:
                nodes=request['advisor']['nodes']*advisor_scale
                if nodes>2_147_483_647:raise ContractError('COMBAT_NODE_BUDGET_EXCEEDS_NATIVE_INTEGER_RANGE')
                request['advisor']['nodes']=nodes
            if boss_budget_ms is not None:request['advisor']['boss_budget_ms']=boss_budget_ms
            if advisor_patch:request['advisor'].update(deepcopy(advisor_patch))
        return request
    @property
    def pending_count(self):return len(self.inflight)+len(self.ready)

    @property
    def allocation_serial(self):
        return self.serial-getattr(self,'f2_probe_evaluations',0)

    @property
    def allocation_pending_count(self):
        return sum(spec['kind']!='f2_readiness_probe'for spec,_,_,_,_ in self.inflight.values())+sum(
            spec['kind']!='f2_readiness_probe'for spec,_,_ in self.ready)

    def tiers_for(self,spec):
        """`policy_prior` of one generated request: the table of the lineage's
        root policy plus the tiers learned on this seed. Sampled at dispatch so
        the learned part uses every result absorbed so far; (solver_seed,
        serial) keeps the draw reproducible."""
        learned=self.prior_provider(self.allocation_serial) if self.prior_provider is not None else {}
        name,_,tag=(spec.get('family')or NATIVE).partition('~')
        table=jitter_tiers(POLICIES[name],CARDS,f'{self.config.solver_seed}:{tag}',self.config.jitter_percent)if tag else POLICIES[name]
        tiers=merge_tiers(table,learned or {})
        # i100: a clinic re-root carries its repair direction for the
        # continuation it starts (tiers only; legality and identity unchanged).
        hint=(spec.get('group')or{}).get('policy_hint')
        return merge_tiers(tiers,hint)if hint else tiers

    def record(self,record):
        record.setdefault('completed_wall_seconds',time.perf_counter()-getattr(self,'wall_started',self.started))
        if os.environ.get('SPIRE_PAUSE_LEDGER'):record.setdefault('completed_active_seconds',perf_counter()-self.started)
        if 'runtime'not in record:record['runtime']=runtime_snapshot()
        self.records.append(record)
        with (self.directory/'evaluations.jsonl').open('a',encoding='utf-8')as ledger:
            ledger.write(json.dumps(record,ensure_ascii=False,allow_nan=False)+'\n')

    def dispatch(self,specs):
        submitted=0
        for spec in specs:
            request=spec['request'];kind=spec['kind']
            if perf_counter()>=self.deadline:break
            if kind!='f2_readiness_probe'and self.allocation_serial>=self.config.evaluations:break
            if kind in SYNTHETIC_KINDS:
                # Synthetic: one disposable worker for the whole batch, no cache, no checkpoint.
                label=f'eval-{self.serial:04d}-{kind}';self.serial+=1;submitted+=1
                if kind=='f2_readiness_probe':self.f2_probe_evaluations+=1
                timeout=max(.1,min(self.config.task_seconds,self.deadline-perf_counter()))
                future=self.pool.submit_probes(spec['requests'],self.directory/label,timeout)
                self.inflight[future]=(spec,None,label,self.directory/label,None)
                if kind=='real_card_menu_probe':self.research_started[future]=perf_counter()
                continue
            isolated=research_consumer(spec)
            if request.get('generate_candidate')and not isolated:
                tiers=self.tiers_for(spec)
                if tiers:request['policy_prior']=tiers
            # Claude review 2(b): ordinary capture sources retain the existing
            # persistent/cache/checkpoint path. Consumers alone replay fresh;
            # an unchanged Progress guardian may reject them as UNKNOWN.
            if isolated:
                request['capture_checkpoints']=False
                request.pop('checkpoint',None)
            self.pool.validate_inputs(request)
            key=canonical({'inputs':self.pool.inputs,'request':request})
            cached=None if isolated else self.cache.get(key)
            label=f'eval-{self.serial:04d}-{kind}';self.serial+=1;submitted+=1
            out=self.directory/label
            if cached is not None:
                result,identity=cached
                record={'label':label,'kind':kind,'cache_hit':True,'prefix_length':len(request['history']),
                        'classification':classify_failure(result),'observation':result.get('observation')}
                record.update(family_fields(spec))
                if spec.get('preparation'):record['preparation']=deepcopy(spec['preparation'])
                if spec.get('macro_route'):record['macro_route']=deepcopy(spec['macro_route'])
                if self.config.tail_mode!='off' or self.config.macro_fair or self.config.macro_plateau or self.config.macro_widening or self.config.macro_routes!='off':
                    if spec.get('group'):record['macro_origin']=deepcopy(spec['group'])
                if(role:=research_role(self.config,spec)):
                    record['research_work']={'role':role,'isolated':False,**measured_research_work(result,cache_hit=True)}
                out.mkdir(parents=True);write_json(out/'cached.json',{'request':request,'result':result,'identity':identity})
                self.record(record)
                self.ready.append((spec,result,label));continue
            request=deepcopy(request)
            cp=self.checkpoints.nearest(request['history']) if self.checkpoints and not isolated else None
            if cp:request['checkpoint']=str(cp.path)
            timeout=max(.1,min(self.config.task_seconds,self.deadline-perf_counter()))
            future=(self.pool.submit(request,out,timeout,fresh=True,disposable=True) if isolated
                    else self.pool.submit(request,out,timeout))
            self.inflight[future]=(spec,None if isolated else key,label,out,cp)
            if research_role(self.config,spec):self.research_started[future]=perf_counter()
        return submitted

    def collect(self,timeout=.2,ordered=False):
        from concurrent.futures import wait,FIRST_COMPLETED,CancelledError
        def order(label):return int(label.split('-')[1])
        target=None
        if ordered:
            sampling=[t for t in self.ready if t[0]['kind']=='f2_readiness_probe']
            candidates=[(order(t[2]),'ready',t)for t in self.ready]
            candidates += [(order(v[2]),'future',f)for f,v in self.inflight.items()]
            ordinary=[row for row in candidates if (row[2][0]['kind'] if row[1]=='ready'
                      else self.inflight[row[2]][0]['kind'])!='f2_readiness_probe']
            candidates=ordinary or candidates
            if not candidates:return []
            _,kind,item=min(candidates,key=lambda x:x[0])
            for row in sampling:self.ready.remove(row)
            if kind=='ready':
                if item in self.ready:self.ready.remove(item)
                return [item]+[row for row in sampling if row is not item]
            target=item;outputs=sampling
        else:outputs=self.ready;self.ready=[]
        if not outputs and self.inflight:
            wait([target]if target is not None else self.inflight,timeout=timeout,return_when=FIRST_COMPLETED)
        finished=[f for f in self.inflight if f.done()and(target is None or f is target
                  or self.inflight[f][0]['kind']=='f2_readiness_probe')]
        # Order simultaneously available completions by submission label. A slow
        # earlier task cannot prevent replenishing the other worker lanes.
        finished.sort(key=lambda f:order(self.inflight[f][2]))
        for future in finished:
            spec,key,label,out,cp=self.inflight.pop(future)
            if spec['kind'] in SYNTHETIC_KINDS:
                outputs.append((spec,self.collect_probes(spec,future,label),label));continue
            try:result,identity=future.result()
            except (WorkerError,OSError,ValueError,CancelledError) as e:
                result={'status':'UNKNOWN','reason':str(e) or 'SEARCH_CANCELLED','trace':[], 'decision_evidence':[],
                        'native_terminal_observed':False,'value':None,'game_equivalence_verified':False}
                try:
                    failure=read_json(out/'failure.json')
                    if isinstance(failure.get('memory_failure'),dict):result['memory_failure']=failure['memory_failure']
                except (OSError,ValueError,TypeError):pass
                identity=None
            if identity:
                if self.identity is None:
                    self.identity=identity
                    self.checkpoints=CheckpointArchive(self.ctx,identity,self.config.checkpoint_mib*1024**2,
                        research_progress=self.research_progress)
                if canonical(identity)!=canonical(self.identity):raise ContractError('worker binary identity mismatch')
                if not research_consumer(spec):self.checkpoints.import_result(result)
                if key is not None and classify_failure(result) not in ('UNKNOWN','MECHANISM_OR_HOST_GAP','RESTORE_OR_REPLAY_MISMATCH','TIMEOUT','RESOURCE_LIMIT','NATIVE_CRASH','INVALID_STATE','SEARCH_CANCELLED'):
                    self.cache.put(key,(result,identity))
            record={'label':label,'kind':spec['kind'],'cache_hit':False,'prefix_length':len(spec['request']['history']),
                'completed_wall_seconds':time.perf_counter()-getattr(self,'wall_started',self.started),'coordinator_cpu_seconds':time.process_time(),
                'runtime':runtime_snapshot(),
                'expanded_combat_nodes':sum(int(s.get('expanded_nodes')or 0)for s in (result.get('advisor_metrics')or{}).get('searches',[])),
                'checkpoint_prefix':cp.prefix_length if cp else 0,'classification':classify_failure(result),
                'reason':result.get('reason'),'actions':len(result.get('trace',[])),
                'observation':result.get('observation'),'performance':result.get('performance'),
                'advisor_metrics':result.get('advisor_metrics'),'repair':spec.get('repair')}
            record.update(family_fields(spec))
            if isinstance(result.get('memory_failure'),dict):
                record['memory_failure']=result['memory_failure']
                if not result.get('advisor_metrics'):record['expanded_combat_nodes']=None
            if result.get('completed_prefix_recovery'):record['completed_prefix_recovery']=result['completed_prefix_recovery']
            if spec.get('preparation'):record['preparation']=deepcopy(spec['preparation'])
            if spec.get('macro_route'):record['macro_route']=deepcopy(spec['macro_route'])
            if self.config.tail_mode!='off' or self.config.macro_fair or self.config.macro_plateau or self.config.macro_widening or self.config.macro_routes!='off':
                if spec.get('group'):record['macro_origin']=deepcopy(spec['group'])
            if isinstance(result.get('map_route_result'),dict):record['map_route_result']=result['map_route_result']
            if(role:=research_role(self.config,spec)):
                start=getattr(self,'research_started',{}).pop(future,None)
                interval=perf_counter()-start if start is not None else None
                record['research_work']={'role':role,'isolated':research_consumer(spec),
                    **measured_research_work(result,submit_to_absorb_seconds=interval)}
            if spec.get('requeued'):record['requeued']=spec['requeued']
            self.record(record);outputs.append((spec,result,label))
            # Append one record rather than rewriting the entire growing ledger.
        return outputs

    def collect_probes(self,spec,future,label):
        """Outcomes of one synthetic probe batch. Nothing of it reaches the
        checkpoint archive, the result cache or a trajectory: only the reduced
        outcomes are handed on, for the gate model."""
        from concurrent.futures import CancelledError
        try:rows=future.result()
        except (WorkerError,OSError,ValueError,CancelledError) as e:rows=[(None,str(e) or 'SEARCH_CANCELLED')]*len(spec['requests'])
        paired=spec['kind']in('paired_card_probe','real_card_menu_probe','f2_readiness_probe')
        reducer=(menu_probe_outcome if spec['kind']=='real_card_menu_probe' else
                 readiness_outcome if spec['kind']=='f2_readiness_probe' else paired_outcome)
        outcomes=[(reducer(decision,spec['requests'][i]) if paired else probe_outcome(decision))
                  if decision is not None and (not paired or i<len(spec['requests'])) else None
                  for i,(decision,_)in enumerate(rows)]
        usable=[o for o in outcomes if o is not None]
        rejections=[menu_probe_rejection(decision,spec['requests'][i],error)if i<len(spec['requests'])and(i>=len(outcomes)or outcomes[i]is None)else None
                    for i,(decision,error)in enumerate(rows)]if spec['kind']=='real_card_menu_probe'else None
        record={'label':label,'kind':spec['kind'],'cache_hit':False,'prefix_length':len(spec['request']['history']),
            'completed_wall_seconds':time.perf_counter()-getattr(self,'wall_started',self.started),'coordinator_cpu_seconds':time.process_time(),
            'classification':'SYNTHETIC_PROBE','synthetic':True,'gate':list(spec['gate']),'table':spec['table'],'base':spec['base'],
            'probes':len(rows),'usable_probes':len(usable),'errors':[e for _,e in rows if e][:3],
            'expanded_combat_nodes':sum(o.get('nodes',0)for o in usable),'probe_search_seconds':sum(o.get('search_seconds',0)for o in usable),
            'observation':None}
        if paired:
            workload=probe_workload(rows,len(spec['requests']))
            record.update(**({'samples':spec['samples']}if spec['kind']=='f2_readiness_probe'else{'sample':spec['sample']}),
                labels=spec['labels'],**workload,
                expanded_combat_nodes=workload['measured_expanded_combat_nodes']if workload['work_measurement_complete']else None,
                probe_search_seconds=workload['measured_probe_search_seconds']if workload['work_measurement_complete']else None,
                probe_native_seconds=workload['measured_probe_native_seconds']if workload['work_measurement_complete']else None)
            if spec['kind']=='f2_readiness_probe':
                record.update(stage=spec['stage'],readiness_entry=spec.get('entry'))
        if spec['kind']=='real_card_menu_probe':
            start=getattr(self,'research_started',{}).pop(future,None)
            record['research_work']={'role':'synthetic_B','isolated':True,'cache_hit':False,'metrics':{
                'native_wall_us':int(workload['measured_probe_native_seconds']*1e6)if workload['work_measurement_complete']else None,
                'expanded_combat_nodes':workload['measured_expanded_combat_nodes']if workload['work_measurement_complete']else None},
                'measured_subsets':{'native_wall_us':int(workload['measured_probe_native_seconds']*1e6),
                                    'expanded_combat_nodes':workload['measured_expanded_combat_nodes']},
                'submit_to_absorb_seconds':perf_counter()-start if start is not None else None,
                'scope':'native batch reports; missing per-arm costs remain unknown'}
            record['outcome_rejections']=rejections
        self.record(record)
        return {'status':'PROBE_BATCH','synthetic':True,'outcomes':outcomes,'trace':[],'decision_evidence':[],
                **({'workload':workload}if paired else{}),**({'rejections':rejections}if rejections is not None else{}),
                **({'arm_workloads':[probe_workload([row],1)for row in rows]}
                   if spec['kind']=='paired_card_probe'and getattr(getattr(self,'config',None),'f2_readiness_probes',False) else{}),
                **({'worker_workload':probe_workload(rows,len(spec['requests']))}
                   if getattr(getattr(self,'config',None),'f2_readiness_probes',False)else{})}

    def batch(self,specs):
        if self.pending_count:raise ContractError('batch cannot overlap streaming requests')
        self.dispatch(specs);outputs=[]
        while self.pending_count:outputs.extend(self.collect())
        return sorted(outputs,key=lambda item:int(item[2].split('-')[1]))

    def cancel_remaining(self):
        if not self.inflight:return
        self.pool.cancel_pending()
        while self.pending_count:self.collect()

    def verify_win(self,result,label):
        if not is_winning_candidate(result):return None
        out=self.directory/('verify-'+label)
        req={k:self.ctx[k] for k in ('seed','character','ascension','unlocks')}
        req.update(command='replay',out=str((out/'data').resolve()),history=result['trace'],
            generate_candidate=False,compact=True,expected_evidence=result['decision_evidence'])
        # Fresh owned process within the SAME resource reservation.
        # Starts from INITIAL run: no advisor, no checkpoint, no result-cache.
        try:
            replay,identity=self.pool.run(req,out,self.config.task_seconds,fresh=True)
            certificate=check_winning_replay(result,replay,{'context':self.ctx,'native':self.identity},
                {'context':self.ctx,'native':identity})
            write_json(self.directory/'certificate.json',certificate)
            # Shared post-verification instrumentation also times the i082 control.
            # This changes no candidate request, choice, budget or proof contract.
            write_json(self.directory/'verification-timing.json', {
                'schema':'spire-verification-timing/v1','candidate_label':label,
                'verified_wall_seconds':time.perf_counter()-getattr(self,'wall_started',self.started),
                'verified_active_seconds':perf_counter()-self.started,
                'scope':'coordinator origin; after successful independent native replay'})
            write_json(self.directory/'winning-route.json',{'context':self.ctx,'trace':result['trace']})
            return certificate
        except (WorkerError,ContractError,OSError) as e:
            write_json(out/'verification-failed.json',{'error':str(e),'status':'UNKNOWN'});return None


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
    from .tail_search import TailFocusScheduler
    from .gate_timing import GateTiming
    from .clinic_focus import ClinicFocusScheduler
    from .gate_clinic import GateClinic
    from .strategy_priors import StructuralPrior
    from .gatemodel import GateModels
    if ctx.get('objective')!=OBJECTIVE:raise ContractError('P5 supports mode1 full-information Boolean objective only')
    if (out/'result.json').exists():raise ContractError('use a fresh output directory')
    out.mkdir(parents=True,exist_ok=True)
    if scope_prefix is not None:
        if config.scheduler!='weighted' or config.root_portfolio!=1:raise ContractError('Diagnostic scope requires indexed weighted scheduling and a single root')
        if canonical(initial_prefix or [])!=canonical(scope_prefix):raise ContractError('Diagnostic initial prefix must equal scope prefix')
    if config.root_policies and (initial_prefix or scope_prefix is not None or stop_floor is not None or config.root_portfolio!=1):
        raise ContractError('root policies need a fresh, unscoped start and a single native root')
    if preparation_enabled(config) and (ctx.get('character')!='IRONCLAD' or ctx.get('ascension')!=10 or ctx.get('unlocks')!='all'
            or initial_prefix or scope_prefix is not None or stop_floor is not None or config.root_portfolio!=1 or not advisor):
        raise ContractError('i075 research needs fresh IRONCLAD A10 all unlocks with combat advisor and one unscoped root')
    ev=(Evaluator(pool,ctx,out,config,advisor) if scope_prefix is None and stop_floor is None else
        Evaluator(pool,ctx,out,config,advisor,scope_prefix=scope_prefix,stop_floor=stop_floor))
    ev.load_archive(import_checkpoints)
    frontier=DiverseFrontier(limit=config.archive_entries)
    # Per-seed gate model: fitted on this run's own native boss fights, used to
    # order options (focus scheduler) and, with config.prior, as rollout tiers.
    probing=bool(config.gate_probe_schedule)
    if probing and (scope_prefix is not None or stop_floor is not None or advisor is None):
        raise ContractError('gate probes need an unscoped search with the combat advisor')
    joint=F2JointModel()if config.f2_joint_model else None
    models=(GateModels(config.solver_seed,probes=probing,f2_joint_model=joint)if joint is not None else
        (GateModels(config.solver_seed,probes=True) if probing else GateModels(config.solver_seed)))if (
        probing or config.prior or config.scheduler=='focus' or joint is not None)else None
    if models is not None:
        models.minimum,models.refresh=config.gate_model_minimum,config.gate_model_refresh
    if joint is not None:
        joint.minimum,joint.refresh=config.gate_model_minimum,config.gate_model_refresh
    if config.prior:ev.prior_provider=models.prior
    paired=PairedCardProbes(config.paired_card_probes,config.solver_seed,context=ctx,inputs=getattr(pool,'inputs',None),
        every=config.paired_card_every,dedup=config.paired_card_dedup,merge=config.paired_card_merge,
        **({'first':config.paired_card_first}if config.paired_card_first!=1 else{}),
        inactive_reason=('no_combat_advisor' if advisor is None else 'scoped_diagnostic' if scope_prefix is not None or stop_floor is not None else None))
    menu_probes=RealCardMenuProbes(config.real_card_menu_probes,config.solver_seed,context=ctx,inputs=getattr(pool,'inputs',None),
        inactive_reason=('no_combat_advisor' if advisor is None else 'scoped_diagnostic' if scope_prefix is not None or stop_floor is not None else None))
    readiness=F2Readiness(config.f2_readiness_probes,config.solver_seed,every=config.f2_readiness_every,
        context=ctx,inputs=getattr(pool,'inputs',None),inactive_reason=(
            'no_combat_advisor'if advisor is None else'scoped_diagnostic'if scope_prefix is not None or stop_floor is not None else None))
    f1_winners=F1WinnerReuse(config.f1_winner_reuse,prefer_hp=config.prefer_f1_hp)
    preparation=PreparationCandidates(config)
    macro_service=MacroService(config.macro_fair,config.macro_aux_burst)
    # Created after the scheduler: its contention test reads the gate clinic.
    governor=None
    macro_routes=MacroRoutePortfolio(config,preparation)
    if config.paired_card_probes and not paired.inactive_reason:
        ev.prior_provider=lambda serial:merge_tiers(models.prior(serial)if config.prior else{},paired.tiers())
    scheduler=(IncumbentScheduler() if config.scheduler=='incumbent' else NativeTreeScheduler(ctx) if config.scheduler=='tree' else
               DepthFirstScheduler() if config.scheduler=='dfs' else
               (ClinicFocusScheduler if config.clinic else TailFocusScheduler if config.tail_mode!="off" else FocusScheduler)(scope_prefix,models=models,elites=config.focus_elites,pool=config.focus_pool,
                              focus=config.focus_share,site_cap=config.site_cap,cluster_cap=config.focus_cluster_cap,
                              cluster_percent=config.focus_cluster_percent,family=config.focus_family,
                              optimism=config.focus_optimism,carry=config.focus_carry,stall=config.focus_stall,
                              stall_extended=config.focus_stall_extended,stall_f2=config.focus_stall_f2,
                              f2_joint_focus=config.f2_joint_focus,plateau_diversity=config.macro_plateau,
                              recover_deferred=config.macro_widening,
                              **({"tail_mode":config.tail_mode,"tail_sites":config.tail_sites,"tail_mib":config.tail_mib,
                                  "tail_cohort_inflight":config.tail_cohort_inflight,"solver_seed":config.solver_seed}
                                 if config.tail_mode!="off" else {}),
                              **({'clinic':GateClinic(min_entries=config.clinic_min_entries,
                                      prior=StructuralPrior() if config.strategy_prior=='builtin' else None),
                                  'root_share':config.clinic_root_share,'undecided_share':config.clinic_undecided_share,
                                  'depth_share':config.clinic_depth_share,'repair_hints':config.clinic_hints} if config.clinic else {})) if config.scheduler=='focus' else
               IndexedStrategicScheduler(scope_prefix))
    gate_clock=GateTiming(config.gate_timing,prefixes=getattr(getattr(scheduler,'explorer',scheduler),'prefixes',None))
    governor=AuxGovernor(config.clinic,quiet=config.clinic_aux_share,contended=config.clinic_aux_contended,
                         contended_fn=getattr(scheduler,'contended',None))
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
    # root_async: root results not absorbed yet, and the size of the first round.
    # requeue_lost: (not before, spec) of lost proposals, and counts by reason.
    root_open=0;root_round=0;held=[];requeued=Counter()
    # Complete traces of fresh rollouts; per kind, how many came back and how many repeated an earlier one.
    fresh_routes=set();fresh_counts=Counter();fresh_repeats=Counter()
    branches=set();rooms=set();completed=0;terminal=0;fresh=0;act2=0;act3=0
    scheduled=Counter()
    def allocation_serial():return getattr(ev,'allocation_serial',ev.serial-getattr(ev,'f2_probe_evaluations',0))

    def ordinary_pending():
        if hasattr(ev,'allocation_pending_count'):return ev.allocation_pending_count
        sampling=sum(row[0]['kind']=='f2_readiness_probe'for row in getattr(ev,'inflight',{}).values())+sum(
            row[0]['kind']=='f2_readiness_probe'for row in getattr(ev,'ready',[]))
        return ev.pending_count-sampling

    def source_record(label):return next((r for r in reversed(ev.records)if r.get('label')==label),{})

    def sync_readiness(updates=None):
        if not config.f2_readiness_probes:return
        gate=models.gates.get((2,0))if models is not None else None
        previous_totals=readiness.f1_life_total,readiness.f2_life_total
        if gate is not None:readiness.set_f1_life_total(gate.total)
        f2=models.gates.get((2,1))if models is not None else None
        if f2 is not None:readiness.set_f2_life_total(f2.total)
        if joint is not None:
            joint.set_life_totals(f1_life_total=readiness.f1_life_total,f2_life_total=readiness.f2_life_total)
        if updates is None or (readiness.f1_life_total,readiness.f2_life_total)!=previous_totals:
            updates=readiness.entry_updates()
        for row in updates:
            if joint is not None and row['f1_raw']is not None and len(row['f2_outcomes'])in(5,10)and all(valid_outcome(o)for o in row['f2_outcomes']):
                joint.observe(row['rawentrykey'],row['observation'],row['f1_raw'],row['f2_outcomes'],
                    f1_life_total=row['f1_life_total'],f2_life_total=row['f2_life_total'],sample_group_key=row['sample_group_key'])
            if config.f2_joint_focus and isinstance(scheduler,FocusScheduler):
                for source in row['sources']:
                    scheduler.record_f2_readiness(source['label'],row['status'],mean=row['mean'])
            if config.clinic and isinstance(scheduler,ClinicFocusScheduler):
                # Synthetic full-HP readiness: allocation evidence for the clinic only.
                for source in row['sources']:
                    scheduler.record_clinic_readiness(source['label'],row['status'],row['sample_group_key'])
        if joint is not None and config.paired_card_joint:
            # i082: add-card arms of complete paired tables (synthetic F2, borrowed real F1).
            for row in paired.joint_rows(readiness.entry_for_source):
                joint.observe(row['entry_key'],row['observation'],row['f1_raw'],row['f2_outcomes'],
                    f1_life_total=row['f1_life_total'],f2_life_total=row['f2_life_total'],sample_group_key=row['sample_group_key'])

    def dead_retry(spec):
        if not config.f2_dead_retry or spec.get('kind')!='gate_retry':return False
        key=readiness.entry_key_for_prefix((spec.get('request')or{}).get('history',[]))
        return (readiness.state_for(key)or{}).get('status')=='DEAD_AT_FULL_HP'

    def native_workload(candidate):
        wall=(candidate.get('performance')or{}).get('wall_us')
        known=type(wall)is int and wall>=0
        return {'measured_probe_native_seconds':wall/1e6 if known else 0,
            'work_measurement_complete':known,'unknown_work_probes':int(not known)}

    def dispatch_specs(specs):
        before=ev.serial;own_before=getattr(ev,'f2_probe_evaluations',0)
        prepare=getattr(scheduler,'prepare_submission',None)
        if prepare is not None:
            for spec in specs:prepare(spec)
        ev.dispatch(specs)
        gate_clock.fitted(models,joint,time.perf_counter()-getattr(ev,"wall_started",ev.started))
        if isinstance(scheduler,TailFocusScheduler):
            for rejected in specs[ev.serial-before:]:scheduler.release_unsubmitted(rejected)
        # Legacy Fake/adaptor dispatch implementations retain their interface;
        # their accepted prefix still identifies exactly which work was sent.
        if getattr(ev,'f2_probe_evaluations',0)==own_before:
            ev.f2_probe_evaluations=own_before+sum(s['kind']=='f2_readiness_probe'for s in specs[:ev.serial-before])

    def next_sampling_stage(planned=()):
        if not config.f2_readiness_probes:return None
        if ev.pending_count>ordinary_pending():return None
        retry_keys=[readiness.entry_key_for_prefix(spec['request'].get('history',[]))for spec in [*planned,*repairs.items]
                    if spec.get('kind')=='gate_retry'and not dispatch_blocked(spec,scheduler)]
        focus_keys=[]
        if isinstance(scheduler,FocusScheduler):
            for source in scheduler._elite_sources():
                if getattr(source,'f1_entry',None)is None:continue
                entries=readiness.entry_for_source(source.label)
                if entries is not None:
                    if not isinstance(entries,list):entries=[entries]
                    focus_keys.extend(row['entry_key']for row in entries if row is not None)
        return readiness.next_stage(retry_entries=retry_keys,focus_entries=focus_keys)
    result={'schema':'spire-p5-result/v1','context':ctx,'configuration':config.__dict__,
        'resources':pool.resources.as_dict(),'native_runtime':getattr(pool,'runtime',None),'status':'UNKNOWN','lower_bound':0,'upper_bound':1,
        'optimal_in_backend':False,'game_equivalence_verified':False,
        'semantic_scope':'bundled native DLL commands in patched offline TestMode',
        'information_mode':'full; NOT a first-attempt public-information evaluation',
        'heuristic_pruning':True,'exclusion_certificates':[], 'evaluations':[]}
    if scope_prefix is not None or stop_floor is not None:
        result['diagnostic_scope']={'prefix_length':len(scope_prefix or []),'stop_floor':stop_floor,'counts_as_fresh_planner_run':False}

    def requeue(spec,candidate):
        """requeue_lost: a proposal lost to memory is otherwise never proposed again."""
        reason=candidate.get('reason')
        if reason not in LOST_REASONS or spec['kind']in SYNTHETIC_KINDS or spec.get('requeued',0)>=config.requeue_lost:return
        again={**spec,'requeued':spec.get('requeued',0)+1,'request':deepcopy(spec['request'])}
        if reason=='NATIVE_TASK_MEMORY_BUDGET':halve_nodes(again['request'])
        held.append((perf_counter()+(REQUEUE_HOLD_SECONDS if reason=='MEMORY_ADMISSION_DENIED' else 0),again))
        requeued[reason]+=1;result['requeued_evaluations']=dict(requeued)

    def absorb(items):
        nonlocal best,best_label,certificate,completed,terminal,fresh,act2,act3
        for spec,candidate,label in items:
            if config.f2_readiness_probes and spec['kind']in SYNTHETIC_KINDS and spec['kind']!='f2_readiness_probe':
                readiness.record_worker_work(label,candidate.get('worker_workload')or candidate.get('workload'))
            if spec['kind']=='f2_readiness_probe':
                readiness.absorb(spec,candidate['outcomes'],candidate.get('workload'),label=label)
                sync_readiness()
                if progress:progress({'label':label,'kind':spec['kind'],'classification':'SYNTHETIC_PROBE','floor':None,'restored_prefix':0})
                continue
            if spec['kind']=='real_card_menu_probe':
                urgent.extend(menu_probes.absorb(spec,candidate['outcomes'],candidate.get('workload'),candidate.get('rejections')))
                if progress:progress({'label':label,'kind':'real_card_menu_probe','classification':'SYNTHETIC_PROBE','floor':None,'restored_prefix':0})
                continue
            if spec['kind']=='paired_card_probe':
                paired.absorb(spec,candidate['outcomes'],candidate.get('workload'))
                if config.f2_readiness_probes:
                    arms=candidate.get('arm_workloads')or[]
                    readiness.adopt_paired(spec,candidate['outcomes'],arms[0]if arms else None)
                    sync_readiness()
                if progress:progress({'label':label,'kind':'paired_card_probe','classification':'SYNTHETIC_PROBE','floor':None,'restored_prefix':0})
                continue
            if spec['kind']=='gate_probe':
                # Synthetic outcomes go to the gate model's probe tables and nowhere else.
                models.add_probes(spec['gate'],spec['table'],list(zip(spec['labels'],candidate['outcomes'])))
                if progress:progress({'label':label,'kind':'gate_probe','classification':'SYNTHETIC_PROBE','floor':None,'restored_prefix':0})
                continue
            if config.requeue_lost:requeue(spec,candidate)
            classification=classify_failure(candidate)
            obs=candidate.get('observation') or {}
            completed_record=source_record(label)
            gate_clock.observe(candidate,completed_record.get('completed_wall_seconds'),cache_hit=completed_record.get('cache_hit',False))
            if config.f2_readiness_probes:
                cached=bool(completed_record.get('cache_hit'))
                readiness.completed_evaluation(label,spec['kind'],synthetic=bool(candidate.get('synthetic')),cache_hit=cached,
                    workload=native_workload(candidate))
            family=spec.get('family')
            if family:families[label]=family
            if spec['kind']in FRESH_KINDS and candidate.get('trace'):
                route=hashlib.sha256(canonical(candidate['trace'])).digest()
                fresh_counts[spec['kind']]+=1;fresh_repeats[spec['kind']]+=route in fresh_routes
                fresh_routes.add(route)
            if candidate.get('trace'):
                completed+=1;branches.add(strategic_trace(candidate));rooms.add(strategic_trace(candidate,True))
                act2+=int(obs.get('act',0)>=1);act3+=int(obs.get('act',0)>=2)
                fresh+=int(not spec['request']['history'] or spec['kind']=='root_rollout')
            terminal+=int(classification in ('NATIVE_ROUTE_DEATH','NATIVE_WIN_CANDIDATE'))
            if models is not None:
                # The parent is the trajectory this request deviated from (or
                # re-solved); the pair feeds the difference model.
                models.add(candidate,label,(spec.get('group')or spec.get('repair')or{}).get('source'))
            gate_clock.fitted(models,joint,completed_record.get("completed_wall_seconds"))
            frontier.add(candidate,label)
            if isinstance(scheduler,FocusScheduler):
                record=next((row for row in reversed(ev.records) if row.get('label')==label),{})
                scheduler.add(candidate,label,spec={**spec,'cache_hit':record.get('cache_hit',False)},
                              completed_wall_seconds=record.get('completed_wall_seconds'))
            else:scheduler.add(candidate,label)
            if config.paired_card_probes and candidate.get('trace'):
                paired_specs=paired.observe(candidate,label,len(spec['request']['history']),ev.request([]))
                urgent.extend(paired_specs)
            else:paired_specs=[]
            if config.f2_readiness_probes and candidate.get('trace'):
                template=ev.request([])
                for name in ('policy_seed','policy_prior','advisor'):
                    if name in spec['request']:template[name]=deepcopy(spec['request'][name])
                    else:template.pop(name,None)
                updates=readiness.observe(candidate,label,len(spec['request']['history']),template,family=family)
                readiness.earmark_paired(paired_specs)
                sync_readiness(updates)
            if config.real_card_menu_probes and candidate.get('trace'):
                template=ev.request([])
                for key in ('policy_seed','policy_prior','advisor'):
                    if key in spec['request']:template[key]=deepcopy(spec['request'][key])
                    else:template.pop(key,None)
                urgent.extend(menu_probes.observe(candidate,label,len(spec['request']['history']),template,family))
            if config.f1_winner_reuse:
                if spec['kind']=='f1_winner_reuse':f1_winners.record_execution(candidate,label,request=spec['request'])
                for proposal in f1_winners.observe(candidate,label,ev.request([]),source_request=spec['request']):
                    proposal['family']=family;urgent.append(proposal)
            if preparation.enabled:
                if spec['request'].get('map_route_plan'):preparation.record_execution(candidate,label)
                urgent.extend(preparation.observe(candidate,label,ev.request([]),spec['request'],family=family,kind=spec['kind']))
            if config.macro_routes!='off':
                macro_routes.observe(candidate,label,ev.request([]),spec['request'],family)
            if config.preserve_completed_prefix and(candidate.get('completed_prefix_recovery')or{}).get('validated'):
                prefix=candidate['trace'];key=canonical(prefix)
                if key not in seen_rollouts and len(prefix)<config.max_decisions:
                    seen_rollouts.add(key);request=ev.request(prefix)
                    for name in ('policy_seed','policy_prior','advisor'):
                        if name in spec['request']:request[name]=deepcopy(spec['request'][name])
                        else:request.pop(name,None)
                    request['capture_checkpoints']=False
                    urgent.append({'kind':'memory_prefix_followup','category':'preparation','family':family,
                                   'memory_prefix_source':label,'request':request})
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
                    seen_rollouts.add(key);urgent.append({'kind':'probe_followup','category':'preparation','family':family,'source':label,'request':ev.request(prefix)})
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
        if fresh_counts:metrics['fresh_routes']={kind:{'rollouts':fresh_counts[kind],'repeats':fresh_repeats[kind]}for kind in sorted(fresh_counts)}
        if config.root_policies:metrics['evaluations_by_root_policy']={name:sum((r.get('family')or NATIVE)==name for r in ev.records if r.get('kind')not in SYNTHETIC_KINDS)for name in portfolio}
        if config.paired_card_probes:metrics['paired_card_probes']=paired.snapshot()
        if config.f2_readiness_probes:
            metrics['f2_readiness']={**readiness.snapshot(),'sampling_evaluations':getattr(ev,'f2_probe_evaluations',0),
                'dead_retry':{'enabled':config.f2_dead_retry,'decisions_changed':repairs.deferred_picks,
                              'only_dead_fallback_picks':repairs.deferred_only_picks},
                'joint_focus':scheduler.snapshot().get('f2_joint_focus',{})if config.f2_joint_focus else{'enabled':False},
                'joint_model':joint.snapshot()if joint is not None else{'enabled':False}}
        if config.real_card_menu_probes:metrics['real_card_menu_probes']=menu_probes.snapshot()
        if config.f1_winner_reuse:metrics['f1_winner_reuse']=f1_winners.snapshot()
        if preparation.enabled:metrics['preparation']=preparation.snapshot()
        if config.gate_timing:metrics['gate_timing']=gate_clock.snapshot()
        if config.clinic:metrics['aux_governor']=governor.snapshot()
        if config.macro_fair or config.macro_routes!='off':metrics['macro_service']=macro_service.snapshot()
        if config.macro_routes!='off':metrics['macro_routes']=macro_routes.snapshot()
        if config.f1_winner_reuse or config.real_card_menu_probes or preparation.enabled or config.memory_telemetry or config.preserve_completed_prefix:metrics['research_costs']=research_cost_summary(ev.records)
        if config.memory_telemetry or config.preserve_completed_prefix:
            metrics['memory_diagnostics']={'resource_failures':sum(r.get('reason')in('NATIVE_TASK_MEMORY_BUDGET','NATIVE_TASK_OUT_OF_MEMORY')for r in ev.records),
                'failures_with_heartbeat':sum(isinstance((r.get('memory_failure')or{}).get('last_memory_heartbeat'),dict)for r in ev.records),
                'recovered_prefixes':sum(bool(r.get('completed_prefix_recovery'))for r in ev.records),
                'prefix_followups':sum(r.get('kind')=='memory_prefix_followup'for r in ev.records),
                'scope':'resource diagnostics and fresh prefix candidates; no feasibility or speedup claim'}
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
            elapsed_seconds=time.perf_counter()-getattr(ev,'wall_started',ev.started),best_label=best_label,
            best_observation=(best or {}).get('observation'),
            unresolved_regions=[{'kind':'all runs not excluded by a proof','upper_bound':1}])
        if os.environ.get('SPIRE_PAUSE_LEDGER'):
            result.update(active_seconds=perf_counter()-ev.started,paused_seconds=paused_seconds(),budget_clock='pause_excluded')
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
        root=([{'kind':'baseline','category':'exploration','request':ev.request(initial_prefix or [])}]+
              [{'kind':'root_policy','category':'exploration','family':name,'request':ev.request([])}for name in config.root_policies])
        jittered=[{'kind':'root_jitter','category':'exploration','family':f'{portfolio[i%len(portfolio)]}~root{i+1}','request':ev.request([])}
                  for i in range(config.root_jitter)]
        if config.root_async:urgent.extend(root+jittered);root_open=len(root);root_round=max(config.root_round,len(root))
        else:run(root);urgent.extend(jittered)
        if config.root_jitter or config.restart_jitter:result['jittered_tables']={'percent':config.jitter_percent,'root_rollouts':config.root_jitter,
            'restarts':config.restart_jitter,'tables_are':'diversity sources; allocation only, never legality or a bound'}
        if config.root_policies:result['root_policies']={'portfolio':list(portfolio),'continuation':'lineage keeps its root policy table',
            'tables_are':'diversity sources; allocation only, never legality or a bound'}
    while not certificate:
        can_schedule=allocation_serial()<config.evaluations and perf_counter()<ev.deadline
        window=config.dispatch_window if config.dispatch_mode=='ordered'else lanes
        if config.tail_mode=='on' and isinstance(scheduler,TailFocusScheduler) and scheduler.feedback_ready:
            # Drain existing stale backlog; never cancel it or reorder absorption.
            window=min(window,max(lanes,lanes*config.tail_feedback_rounds))
        free=max(0,window-ordinary_pending())
        if root_open:free=max(0,root_round-allocation_serial())
        if held:
            now=perf_counter()
            urgent.extend(again for due,again in held if due<=now);held[:]=[h for h in held if h[0]>now]
        specs=[]
        if can_schedule:
            # i100: gate retries of a DEPTH-diagnosed stem may use a
            # larger share; ROOT-stem retries go last (never dropped).
            retry_share=.2
            if config.clinic and repairs and any(scheduler.retry_rank(item)==0 for item in repairs.items):
                retry_share=config.clinic_retry_share/100
            for slot in range(min(free,config.evaluations-allocation_serial())):
                # The legacy ordering is identical when macro_fair is off.
                # Root async keeps its historical first-round/barrier contract.
                group=scheduler.next() if not root_open and macro_service.due() else None
                forced=group is not None
                if group is None:
                    selected=take_dispatchable(urgent,scheduler,governor.allow if governor.enabled else None)
                    if selected is not None:
                        specs.append(selected);macro_service.record(selected['kind']);governor.record(selected);continue
                    route=macro_routes.next(scheduler,macro_service.total) if not root_open else None
                    if route is not None:
                        specs.append(route);macro_service.record(route['kind']);governor.record(route);continue
                    probes=(scheduled['combat_probe']+scheduled['gate_retry']
                            +sum(s['kind'] in ('combat_probe','gate_retry') for s in specs))
                    if repairs and (probes==0 or probes<retry_share*(sum(scheduled.values())-scheduled['f2_readiness_probe']+len(specs)+1)):
                        if config.clinic:
                            repair=repairs.popleft(blocked=lambda spec:dispatch_blocked(spec,scheduler),
                                deferred=lambda spec:(config.f2_dead_retry and dead_retry(spec)) or scheduler.retry_rank(spec)==2,
                                rank=scheduler.retry_rank)
                        else:
                            repair=repairs.popleft(blocked=lambda spec:dispatch_blocked(spec,scheduler),
                                deferred=dead_retry if config.f2_dead_retry else None)
                        if repair is not None:
                            specs.append(repair);macro_service.record(repair['kind']);governor.record(repair);continue
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
                    macro_service.record('macro_'+group['category'],core=True,forced=forced)
                    governor.record(specs[-1])
                else:
                    # Restarts rotate through the root policies (native only without a portfolio).
                    family=portfolio[restarts%len(portfolio)]if restarts%len(portfolio)else None
                    if config.restart_jitter:family=f'{family or NATIVE}~restart{restarts+1}'
                    specs.append({'kind':'restart','category':'exploration','family':family,
                                  'request':ev.request(scope_prefix or [],policy=allocation_serial()+slot+1)})
                    restarts+=1
                    macro_service.record('restart')
                    governor.record(specs[-1])
        if not root_open and perf_counter()<ev.deadline:
            sampling=next_sampling_stage(specs)
            if sampling is not None:specs.append(sampling)
        if specs:
            scheduled.update(s['kind'] for s in specs);dispatch_specs(specs)
        if not ev.pending_count:
            # requeue_lost: admission denials on hold keep the run alive while it may schedule.
            if not held or not can_schedule:break
            time.sleep(.2);continue
        trials=ev.collect(ordered=True) if config.dispatch_mode=='ordered'else ev.collect()
        if config.dispatch_mode=='batch':
            while ev.pending_count:trials.extend(ev.collect())
            trials.sort(key=lambda item:int(item[2].split('-')[1]))
        if not trials:continue
        absorb(trials);save()
        if root_open:root_open=max(0,root_open-sum(spec['kind']in('baseline','root_policy')for spec,_,_ in trials))
        if certificate:
            ev.cancel_remaining();save();break
        viable=[t for t in trials if t[0]['kind'].startswith('macro_') and
                classify_failure(t[1]) in ('SEARCH_BUDGET','DECISION_BOUNDARY')]
        for spec,candidate,label in sorted(viable,key=lambda x:utility(x[1]),reverse=True)[:config.survivors]:
            prefix=candidate.get('trace',[]);key=canonical(prefix)
            if prefix and len(prefix)<config.max_decisions and key not in seen_rollouts:
                seen_rollouts.add(key);urgent.append({'kind':'deepen','category':'exploration','family':families.get(label),'source':label,'request':ev.request(prefix)})
    result['stop_reason']='verified_boolean_maximum' if certificate else ('time_budget' if perf_counter()>=ev.deadline else 'evaluation_budget')
    save(force=True);return result
