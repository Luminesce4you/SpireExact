"""Validated, immutable request and scheduling budgets for i054a.

Fields and validation are mechanically extracted from the frozen holdout-01
implementation. Defaults are part of the reproducibility contract.
"""
from __future__ import annotations
from dataclasses import dataclass
from .policies import resolve as resolve_policies

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
    def __post_init__(self):
        if any(type(v) is not int or v<1 for k,v in self.__dict__.items() if k not in ('scheduler','dispatch_mode','repair_mode','solver_seed','low_io','event_driven_settle','prior','gate_retry_plans','gate_retry_percent','root_policies','focus_cluster_cap','focus_cluster_percent','focus_family','focus_optimism','focus_carry','gate_probe_schedule','gate_probe_plan')):
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
        if type(self.focus_optimism) is not int or not 0<=self.focus_optimism<=400:raise ValueError('focus optimism must be an integer percentage within 0..400')
        if self.scheduler not in ('weighted','dfs','tree','incumbent','focus'):raise ValueError('unknown strategic scheduler')
        if self.dispatch_mode not in ('batch','stream','ordered'):raise ValueError('unknown dispatch mode')
        if self.repair_mode not in ('fifo','deep_boss','deep_final_boss','gate'):raise ValueError('unknown repair mode')
        if type(self.prior) is not bool:raise ValueError('prior must be a boolean')
        if type(self.gate_retry_plans) not in (tuple,list) or any(type(p) is not dict for p in self.gate_retry_plans):
            raise ValueError('gate retry plans must be a sequence of advisor patches')
        if self.focus_pool<self.focus_elites:raise ValueError('focus pool must hold every elite')

