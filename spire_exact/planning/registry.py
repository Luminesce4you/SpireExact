"""Mechanism registry: every planner mechanism, its status and its evidence.

Statuses:
- validated-as-i054a: part of the i054a-shm configuration, validated only as
  that whole (holdout-01: 17 / 20 seeds solved within 30 min, iteration-055);
  no ablation of its own.
- validated: has a paired real-game efficacy test of its own (none yet).
- infra: changes speed or resource use, not search decisions; accepted by
  request-for-request identity, not by search results.
- experimental: built, without real-game paired evidence of benefit.
- retired: tested and not kept, or superseded.

The default configuration (configs/i054a-shm.toml) may only activate
DEFAULT_ALLOWED statuses (tests/test_refactor_plugins.py). Nothing added after
i054a has a real-game paired efficacy test (2026-10-07 audit), so every
plugin starts as experimental.

Every option of the planner's command line is classified exactly once: as
run identity, as a budget / resource setting, or as an option of one
mechanism (the test checks this against the parser).
"""
from __future__ import annotations
from dataclasses import dataclass, field

VALIDATED_AS_I054A = 'validated-as-i054a'
VALIDATED = 'validated'
INFRA = 'infra'
EXPERIMENTAL = 'experimental'
RETIRED = 'retired'
STATUSES = (VALIDATED_AS_I054A, VALIDATED, INFRA, EXPERIMENTAL, RETIRED)
DEFAULT_ALLOWED = frozenset({VALIDATED_AS_I054A, VALIDATED, INFRA})

I054A = 'i054a as a whole: experiments/iteration-055/results.md (holdout-01, 17 / 20 within 30 min); no ablation of its own'


def _non_default_solver(name):
    if not name:
        return False
    from ..solvers import default_name
    return name != default_name()


@dataclass(frozen=True)
class Entry:
    name: str
    status: str
    evidence: str
    # Parser destinations that belong to this mechanism.
    options: tuple = ()
    # Whether the mechanism is on for parsed arguments (plugins: when listed).
    active: object = field(default=None, compare=False)
    # Plugins only: 'module:Class' of the plugin.
    target: str | None = None


def _entries(*entries):
    table = {}
    for entry in entries:
        if entry.status not in STATUSES:
            raise ValueError(f'{entry.name}: unknown status {entry.status}')
        if entry.name in table:
            raise ValueError('registered twice: ' + entry.name)
        table[entry.name] = entry
    return table


MECHANISMS = _entries(
    Entry('focus-scheduler', VALIDATED_AS_I054A,
          'AGENTS.md 1.1 point 5 (iteration-037); ' + I054A,
          ('scheduler', 'focus_elites', 'focus_pool', 'focus_share', 'site_cap'), lambda a: a.scheduler == 'focus'),
    Entry('legacy-schedulers', RETIRED,
          'weighted / dfs / tree / incumbent: AGENTS.md 1 point 9 and 1.1 point 2 (superseded by focus)',
          (), lambda a: a.scheduler != 'focus'),
    Entry('gate-model-prior', VALIDATED_AS_I054A,
          'AGENTS.md 1.1 point 5; gate model bench in iteration-048; ' + I054A,
          ('prior',), lambda a: a.prior),
    Entry('gate-preset-escalate', VALIDATED_AS_I054A,
          'gates.py docstring (iteration-037 component evidence); ' + I054A,
          ('gate_preset',), lambda a: a.gate_preset != 'none'),
    Entry('gate-retry', VALIDATED_AS_I054A,
          'iteration-037 component evidence (13 / 15 rescues at 50 %); iteration-045; ' + I054A,
          ('repair_mode', 'gate_retry_percent'), lambda a: a.repair_mode == 'gate'),
    Entry('combat-probe-repairs', RETIRED,
          'fifo / deep_boss / deep_final_boss repairs: AGENTS.md 1 point 9 (i016), replaced by gate retry',
          ('repair_window', 'boss_repair_ms'), lambda a: a.repair_mode != 'gate'),
    Entry('final-gate-plan-open', VALIDATED_AS_I054A,
          'iteration-047 (60 F1 entries: 22 -> 39 passed); ' + I054A,
          ('final_gate_plan',), lambda a: a.final_gate_plan != 'none'),
    Entry('root-policies', VALIDATED_AS_I054A,
          'iteration-043 / 044 (AGENTS.md 1.2 point 1); ' + I054A,
          ('root_policies',), lambda a: bool(a.root_policies)),
    Entry('focus-cluster-cap', VALIDATED_AS_I054A,
          'iteration-044 offline replay (AGENTS.md 1.2 point 2); ' + I054A,
          ('focus_cluster_cap', 'focus_cluster_percent'), lambda a: a.focus_cluster_cap > 0),
    Entry('focus-family', RETIRED,
          'iteration-051: whole-run result negative (AGENTS.md 1.2 point 6)',
          ('focus_family',), lambda a: a.focus_family),
    Entry('focus-optimism', RETIRED,
          'iteration-051: whole-run result negative (AGENTS.md 1.2 point 6)',
          ('focus_optimism',), lambda a: a.focus_optimism > 0),
    Entry('focus-carry', EXPERIMENTAL,
          'iteration-054: two whole runs, no visible benefit (AGENTS.md 1.2 point 7)',
          ('focus_carry',), lambda a: a.focus_carry),
    Entry('gate-probes', EXPERIMENTAL,
          'iteration-044: one-card probe tables rank worse than the gate model; no native end-to-end run',
          ('gate_probe_schedule', 'gate_probe_cards', 'gate_probe_chunk', 'gate_probe_plan'), lambda a: bool(a.gate_probe_schedule)),
    Entry('root-portfolio', RETIRED,
          'i020 0 / 4 (AGENTS.md 1 point 9)',
          ('root_portfolio',), lambda a: a.root_portfolio > 1),
    Entry('combat-advisor', VALIDATED_AS_I054A,
          'CombatSolver bridge (BeamAdvisor.cs); normal-nodes 10,000 = 60,000 on 58 / 58 fights (AGENTS.md 1.1 point 5); ' + I054A,
          ('advisor', 'budget_ms', 'boss_budget_ms', 'profile', 'nodes', 'normal_nodes'), lambda a: a.advisor == 'combatsolver'),
    Entry('combat-solver-version', EXPERIMENTAL,
          'another configs/solvers.json entry (e.g. CombatSolver v0.50.1); no paired real-game test',
          ('combat_solver',), lambda a: _non_default_solver(a.combat_solver)),
    Entry('beam-override', EXPERIMENTAL,
          'upstream beam override; no evidence',
          ('beam',), lambda a: a.beam is not None),
    Entry('complete-continuations-only', RETIRED,
          'i017 (AGENTS.md 1 point 9)',
          ('complete_continuations_only',), lambda a: a.complete_continuations_only),
    Entry('no-continuation-reuse', EXPERIMENTAL,
          'diagnostic switch; no evidence',
          ('no_continuation_reuse',), lambda a: a.no_continuation_reuse),
    Entry('shared-worker-memory', INFRA,
          'i101 port to i054a (one commit budget); no paired test against the hard per-worker policy',
          ('worker_memory_policy',), lambda a: a.worker_memory_policy == 'shared'),
    Entry('event-driven-settle', INFRA,
          'Codex infra (docs/INFRA_HANDOFF.md); independent win replay keeps the reference executor',
          ('event_driven_settle',), lambda a: a.event_driven_settle),
    Entry('ordered-dispatch', INFRA,
          'results absorbed in submission order (AGENTS.md 3.2 reproducibility)',
          ('dispatch',), lambda a: a.dispatch == 'ordered'),
    Entry('stream-or-batch-dispatch', EXPERIMENTAL,
          'absorption in completion order: not reproducible (AGENTS.md 3.2)',
          (), lambda a: a.dispatch != 'ordered'),
)

PLUGINS = _entries(
    Entry('p2-permuted-predictor', EXPERIMENTAL,
          'iteration-106 plan F0-3 (null control of the gate model); not run',
          (), None, 'spire_exact.planning.plugins.permuted:PermutedPredictor'),
    Entry('p1-f2-reroll', EXPERIMENTAL,
          'iteration-064 (76 % of F1 entries before a lost F2 had another winning forecast); iteration-106 plan; '
          'needs host capability gate_plans.final_boss_ordinal; not run',
          (), None, 'spire_exact.planning.plugins.reroll:F2Reroll'),
)

# Run identity: what is solved and where; never a mechanism.
IDENTITY = ('seed', 'character', 'ascension', 'unlocks', 'game_dir', 'out', 'prefix', 'import_checkpoints',
            'solver_seed', 'config', 'plugin', 'component', 'component_gate', 'gate_library')

# Budgets and resources: how much and how fast, not what the search decides.
SETTINGS = ('evaluations', 'seconds', 'task_seconds', 'max_decisions', 'lookahead_actions', 'lookahead_floors',
            'alternatives', 'survivors', 'workers', 'dop', 'reserve_mib', 'worker_memory_mib', 'worker_max_jobs',
            'queue_policy', 'runtime_profile', 'dispatch_window', 'snapshot_stride', 'low_io', 'checkpoint_mib',
            'cache_mib', 'archive_entries')

# SearchConfig fields whose parser destination has another name.
CONFIG_FIELDS = {'max_repair_window': 'repair_window', 'dispatch_mode': 'dispatch',
                 'gate_retry_plans': 'gate_preset', 'gate_probe_plan': 'gate_probe_plan'}


def classified():
    """Parser destination -> 'identity', 'setting' or the mechanism's name."""
    table = {}
    def put(dest, owner):
        if dest in table:
            raise ValueError(f'{dest} is classified twice ({table[dest]}, {owner})')
        table[dest] = owner
    for dest in IDENTITY:
        put(dest, 'identity')
    for dest in SETTINGS:
        put(dest, 'setting')
    for entry in MECHANISMS.values():
        for dest in entry.options:
            put(dest, entry.name)
    return table


def active(args):
    """Entries active for parsed arguments: mechanisms, then listed plugins."""
    from .plugins import parse
    on = [entry for entry in MECHANISMS.values() if entry.active(args)]
    on += [PLUGINS[name] for name, _ in map(parse, getattr(args, 'plugin', None) or []) if name in PLUGINS]
    return on


def not_allowed(args, allowed=DEFAULT_ALLOWED):
    """(name, status) of every active entry whose status is not allowed."""
    return [(entry.name, entry.status) for entry in active(args) if entry.status not in allowed]
