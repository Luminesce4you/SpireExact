"""Usage: python -m spire_exact.planning --help"""
import argparse,json,os
from pathlib import Path
from ..native import game_data
from ..mode1 import context,NativeCampaignBackend
from .io import read_json,write_json
from .pool import NativePool,RUNTIME_PROFILES,MEMORY_POLICIES
from .resources import ResourcePlan
from .search import SearchConfig,solve
from .gates import PRESETS,preset
from .policies import NATIVE,POLICIES,resolve as resolve_policies
from .final_defaults import resolve_entry_defaults,F2_READINESS_EVERY_DEFAULT,FEATURE_PROFILES,PUBLIC_PROFILE

def build_parser():
    p=argparse.ArgumentParser(description='P0–P5 full-information native witness planner; no UNSAT claims')
    p.add_argument('--seed',default='42');p.add_argument('--character',default='IRONCLAD')
    p.add_argument('--ascension',type=int);p.add_argument('--unlocks',choices=['all','none'],default='all')
    p.add_argument('--feature-profile',choices=list(FEATURE_PROFILES),default=PUBLIC_PROFILE,
                   help='i082 (default) is the whole entry: the i075 nine, the i081 four, the i080 run flags and '
                        'denser paired card tables (final_defaults.I082_PLANNER); earlier profiles remain explicit controls')
    p.add_argument('--game-dir',type=Path);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--prefix',type=Path,help='JSON array, request.json/history, decision.json/trace, or winning-route.json')
    p.add_argument('--import-checkpoints',type=Path,action='append',default=[])
    p.add_argument('--advisor',choices=['none','combatsolver'],default='combatsolver')
    p.add_argument('--budget-ms',type=int,default=300);p.add_argument('--boss-budget-ms',type=int,default=1200)
    p.add_argument('--profile',choices=['Low','Medium','High','VeryHigh','Custom'],default='Low')
    p.add_argument('--nodes',type=int);p.add_argument('--no-continuation-reuse',action='store_true')
    p.add_argument('--complete-continuations-only',action='store_true')
    p.add_argument('--beam',type=int,help='Optional upstream combat beam override; remains heuristic')
    p.add_argument('--workers',type=int);p.add_argument('--dop',type=int,default=1)
    p.add_argument('--runtime-profile',choices=RUNTIME_PROFILES,default=None,help='legacy unless the feature profile sets one')
    p.add_argument('--queue-policy',choices=['fifo','short-prefix-first'],default='fifo')
    p.add_argument('--worker-memory-policy',choices=MEMORY_POLICIES,default='hard')
    p.add_argument('--memory-telemetry',action=argparse.BooleanOptionalAction,default=None,
                   help='GC/private-memory heartbeat and resource-abort diagnostics (on in i075-final)')
    p.add_argument('--preserve-completed-prefix',action=argparse.BooleanOptionalAction,default=None,
                   help='Recover completed native prefixes after memory abort; fresh replay only (on in i075-final)')
    p.add_argument('--worker-max-jobs',type=int,default=32)
    p.add_argument('--worker-memory-mib',type=int,default=None,help='900 unless the feature profile sets one');p.add_argument('--reserve-mib',type=int,default=512)
    p.add_argument('--evaluations',type=int,default=24);p.add_argument('--seconds',type=int,default=600)
    p.add_argument('--task-seconds',type=int,default=90);p.add_argument('--max-decisions',type=int,default=2000)
    p.add_argument('--lookahead-actions',type=int,default=80);p.add_argument('--lookahead-floors',type=int,default=2)
    p.add_argument('--alternatives',type=int,default=6);p.add_argument('--survivors',type=int,default=2)
    p.add_argument('--repair-window',type=int,default=32)
    p.add_argument('--repair-mode',choices=['fifo','deep_boss','deep_final_boss','gate'],default=None,
                   help="'gate' re-solves fatal fights with the gate preset's retry plans, best trajectory first")
    p.add_argument('--boss-repair-ms',type=int,default=30000)
    p.add_argument('--root-portfolio',type=int,default=1,choices=[1,2,3,4],help='Parallel rollouts of actual native opening choices; heuristic')
    p.add_argument('--scheduler',choices=['weighted','dfs','tree','incumbent','focus'],default=None,
                   help="'focus' spends most evaluations next to the failures of the best trajectories")
    p.add_argument('--focus-elites',type=int,default=8);p.add_argument('--focus-pool',type=int,default=48)
    p.add_argument('--gate-timing',action=argparse.BooleanOptionalAction,default=None)
    p.add_argument('--clinic',action=argparse.BooleanOptionalAction,default=None,
                   help='i100: gate clinic root-versus-depth diagnosis steers focus picks and retries (allocation only)')
    p.add_argument('--clinic-min-entries',type=int,default=6,help='Distinct entries of a (stem, gate) before a verdict')
    p.add_argument('--clinic-root-share',type=int,default=85,help='Percent of a ROOT stem\'s focus picks that re-root')
    p.add_argument('--clinic-undecided-share',type=int,default=34,help='Percent of an undecided stem\'s focus picks that re-root')
    p.add_argument('--clinic-depth-share',type=int,default=10,help='Percent of a DEPTH stem\'s focus picks that re-root')
    p.add_argument('--clinic-retry-share',type=int,default=35,help='Gate-retry share while a DEPTH stem has pending retries')
    p.add_argument('--clinic-aux-share',type=int,default=10,help='Auxiliary dispatch share before a gate is contested')
    p.add_argument('--clinic-aux-contended',type=int,default=30,help='Auxiliary dispatch share once a gate is contested')
    p.add_argument('--clinic-hints',action=argparse.BooleanOptionalAction,default=None,
                   help='i100: re-root picks carry tiers toward options that repair the diagnosed structural deficit')
    p.add_argument('--strategy-prior',choices=['off','builtin'],default=None,
                   help='Version-robust structural strategy prior read by the gate clinic (i100: builtin)')
    p.add_argument('--paired-card-first',type=int,default=None,help='Distinct final-F1 entries before the first paired table (i100: 8, when paired tables are on)')
    p.add_argument('--tail-mode',choices=['off','shadow','on'],default=None)
    p.add_argument('--tail-sites',type=int,default=1024)
    p.add_argument('--tail-mib',type=int,default=16)
    p.add_argument('--tail-cohort-inflight',type=int,default=2)
    p.add_argument('--tail-feedback-rounds',type=int,default=2)
    p.add_argument('--gate-model-minimum',type=int,default=24)
    p.add_argument('--gate-model-refresh',type=int,default=16)
    p.add_argument('--macro-plateau',action=argparse.BooleanOptionalAction,default=None)
    p.add_argument('--macro-widening',action=argparse.BooleanOptionalAction,default=None)
    p.add_argument('--macro-fair',action=argparse.BooleanOptionalAction,default=None)
    p.add_argument('--macro-routes',choices=['off','shadow','on'],default=None)
    p.add_argument('--macro-aux-burst',type=int,default=4)
    p.add_argument('--macro-route-limit',type=int,default=3)
    p.add_argument('--macro-route-every',type=int,default=8)
    p.add_argument('--macro-route-paths',type=int,default=256)
    p.add_argument('--macro-route-expansions',type=int,default=4096)
    p.add_argument('--macro-route-queue-mib',type=int,default=16)
    p.add_argument('--focus-share',type=int,default=3,help='Focus evaluations per explorer evaluation')
    p.add_argument('--site-cap',type=int,default=12,help='Explorer-side deferral for very wide menus (focus scheduler)')
    p.add_argument('--prior',action=argparse.BooleanOptionalAction,default=None,help='Send per-seed gate-model tiers with every generated rollout (allocation only)')
    p.add_argument('--gate-preset',choices=sorted(PRESETS),default=None,help='Multi-member tactical plan for boss/elite fights')
    p.add_argument('--final-gate-plan',default=None,
                   help="Plan of the gate preset for boss fights of the last act ('open': every boss member runs); 'none' uses the boss plan")
    p.add_argument('--normal-nodes',type=int,help='Node budget for ordinary (non-gate) fights; default is --nodes')
    p.add_argument('--gate-retry-percent',type=int,default=50,
                   help='Retry a fatal fight only after this share of the current enemy HP (or an enemy life) was removed')
    p.add_argument('--root-policies',default=None,
                   help='Comma-separated extra root rollout policies ('+', '.join(sorted(set(POLICIES)-{NATIVE}))+'); lineages keep their policy table')
    p.add_argument('--focus-cluster-cap',type=int,default=None,help='Focus scheduler: elites allowed per deck family (0 = no limit)')
    p.add_argument('--focus-cluster-percent',type=int,default=75,help='Multiset Jaccard percentage from which two final decks are one family')
    p.add_argument('--focus-family',action='store_true',
                   help="Focus scheduler: weight every elite by its deck family's record at the gate it still has to pass (allocation only)")
    p.add_argument('--focus-optimism',type=int,default=0,
                   help='Focus scheduler: percentage of one standard error added to options the gate model has seen little of (0 = off)')
    p.add_argument('--focus-carry',action='store_true',
                   help='Focus scheduler: elite places alternate between the usual order and one that ranks deaths in a later boss of an act by the HP carried in')
    p.add_argument('--focus-stall',type=int,default=None,
                   help='Focus scheduler: distinct entries without a pass from which the first boss of a later act counts as stalled; '
                        'focus picks then alternate with the act before (0 = off)')
    p.add_argument('--root-async',action=argparse.BooleanOptionalAction,default=None,
                   help='The root batch is no barrier: the first round is --root-round evaluations, restarts after the root rollouts (iteration-058)')
    p.add_argument('--root-round',type=int,default=7,
                   help='With --root-async: size of the first round, at least the root batch; fixed so that the path does not depend '
                        'on the number of workers (7 = the round of the default 7 workers; iteration-069)')
    p.add_argument('--requeue-lost',type=int,default=None,
                   help='Dispatch an evaluation lost to memory admission or the worker memory budget again, at most this many times '
                        '(a budget stop with half the combat node budgets; 0 = off)')
    p.add_argument('--restart-jitter',action='store_true',
                   help='Each restart gets a jittered policy table (see --jitter-percent); its lineage keeps it (iteration-061)')
    p.add_argument('--root-jitter',type=int,default=0,
                   help='This many extra fresh rollouts with jittered policy tables, first in line after the root batch (0 = off; iteration-061)')
    p.add_argument('--jitter-percent',type=int,default=20,
                   help='Jittered tables move about this percentage of the card tiers by one or two (iteration-061)')
    p.add_argument('--gate-probe-schedule',default='',
                   help='Distinct real entries of a boss at which a table of synthetic one-card probes is run, e.g. 8,64,512 (empty = off)')
    p.add_argument('--gate-probe-cards',type=int,default=40,help='Cards a probe table tries to add (the ones this seed offered most)')
    p.add_argument('--gate-probe-chunk',type=int,default=8,help='Probes per disposable worker')
    p.add_argument('--gate-probe-plan',choices=['light','run'],default='light',
                   help="'light': the gate preset's probe plan (first boss member only); 'run': the run's own gate plan")
    p.add_argument('--paired-card-probes',action=argparse.BooleanOptionalAction,default=None,
                   help='Five-sample paired synthetic F2 card ordering at real final F1 entries (on; --no-paired-card-probes disables it)')
    p.add_argument('--paired-card-every',type=int,default=None,
                   help='Paired tables at distinct final F1 entries 1, N, 2N, ... (32 in i082; 0 = the former 1, 32, 128, 512)')
    p.add_argument('--paired-card-dedup',action=argparse.BooleanOptionalAction,default=None,
                   help='Skip a paired table whose deck, relics and candidates equal an earlier one; the threshold stays due (on in i082)')
    p.add_argument('--paired-card-merge',choices=['latest','mean'],default=None,
                   help="Paired card signal: 'mean' over complete tables (i082) or the 'latest' table")
    p.add_argument('--paired-card-joint',action=argparse.BooleanOptionalAction,default=None,
                   help='Add-card arms of complete paired tables become extra F2 joint-model rows (i082: on with the joint model and readiness probes)')
    p.add_argument('--f2-readiness-probes',action=argparse.BooleanOptionalAction,default=None)
    p.add_argument('--f2-dead-retry',action=argparse.BooleanOptionalAction,default=None)
    p.add_argument('--f2-joint-focus',action=argparse.BooleanOptionalAction,default=None)
    p.add_argument('--f2-joint-model',action=argparse.BooleanOptionalAction,default=None)
    p.add_argument('--f2-readiness-every',type=int,default=F2_READINESS_EVERY_DEFAULT,
                   help='Non-synthetic native completions per readiness stage; K=20 is a taskbook estimate')
    p.add_argument('--f1-winner-reuse',action=argparse.BooleanOptionalAction,default=None,
                   help='After a real F2 loss execute other already computed F1 winning routes (on in i075-final)')
    p.add_argument('--prefer-f1-hp',action=argparse.BooleanOptionalAction,default=None,
                   help='Prefer projected HP among F1 winning members and A candidates; no added members (on in i075-final)')
    p.add_argument('--real-card-menu-probes',action=argparse.BooleanOptionalAction,default=False,
                   help='Experimental: five paired legal third-act card-menu continuations and separate real followup (off)')
    p.add_argument('--gold-shop-routes',action=argparse.BooleanOptionalAction,default=None,
                   help='Propose reachable shop routes when gold meets the threshold (on in i075-final)')
    p.add_argument('--low-hp-routes',action=argparse.BooleanOptionalAction,default=None,
                   help='Propose third-act routes with fewer elites below the HP threshold (on in i075-final)')
    p.add_argument('--low-hp-routes-any-act',action=argparse.BooleanOptionalAction,default=None,
                   help='i080: extend enabled low-HP routes to the current act boss (on in i082)')
    p.add_argument('--focus-stall-extended',action=argparse.BooleanOptionalAction,default=None,
                   help='i080: per-lineage distinct boss-entry stall and earlier-act backoff; requires positive focus-stall (on in i082)')
    p.add_argument('--forge-menu-dedup',choices=['exact','threshold'],default='exact',
                   help='Forge proposal grouping: exact HP or 50-percent band; both retain max HP')
    p.add_argument('--focus-stall-f2',action=argparse.BooleanOptionalAction,default=None,
                   help='i080: separate opt-in to F2 backoff; requires extended focus-stall (on in i082)')
    p.add_argument('--lean-third-act',action=argparse.BooleanOptionalAction,default=None,
                   help='Propose legal third-act SMITH choices; skip proposals removed in i080 (on in i075-final)')
    p.add_argument('--shop-preparation',action=argparse.BooleanOptionalAction,default=None,
                   help='At route target shops fill potion slots, remove once, buy relics, then existing policy (on in i075-final)')
    p.add_argument('--resource-telemetry',action=argparse.BooleanOptionalAction,default=None,
                   help='Record actual potion, gold and HP events (on in i075-final)')
    p.add_argument('--gold-shop-threshold',type=int,default=200,
                   help='Gold threshold for experimental shop-route proposals')
    p.add_argument('--low-hp-percent',type=int,default=50,
                   help='HP percentage threshold for experimental low-HP route proposals')
    p.add_argument('--preparation-limit',type=int,default=4,
                   help='Maximum experimental shop preparation proposals per source')
    p.add_argument('--dispatch',choices=['batch','stream','ordered'],default='stream')
    p.add_argument('--dispatch-window',type=int,default=56)
    p.add_argument('--snapshot-stride',type=int,default=7)
    p.add_argument('--solver-seed',type=int,default=0)
    p.add_argument('--low-io',action='store_true')
    p.add_argument('--event-driven-settle',action='store_true',help='Experimental native ready-queue settling; independent win replay retains reference executor')
    p.add_argument('--checkpoint-mib',type=int,default=512)
    p.add_argument('--cache-mib',type=int,default=64)
    p.add_argument('--archive-entries',type=int,default=48)
    return p


def parse_planner(argv=None):
    """Parse, resolve the feature profile and validate; touches no native input.
    Returns (parser, args, gate preset, probe schedule, root policies)."""
    p=build_parser()
    a=resolve_entry_defaults(p.parse_args(argv))
    if (a.macro_plateau or a.macro_widening or a.macro_fair) and a.scheduler!='focus':
        p.error('i085 macro allocation switches require --scheduler focus')
    if a.clinic and (a.scheduler!='focus' or a.tail_mode!='off' or a.focus_stall):
        p.error('--clinic needs --scheduler focus, --tail-mode off and --focus-stall 0')
    if a.strategy_prior!='off' and not a.clinic:p.error('--strategy-prior is read by --clinic')
    if a.clinic_hints and (not a.clinic or a.strategy_prior=='off'):p.error('--clinic-hints need --clinic and a strategy prior')
    if a.paired_card_first<1:p.error('--paired-card-first must be positive')
    if a.clinic_min_entries<3 or any(not 0<=v<=100 for v in (a.clinic_root_share,a.clinic_undecided_share,a.clinic_depth_share,
                                                         a.clinic_retry_share,a.clinic_aux_share,a.clinic_aux_contended)):
        p.error('--clinic-min-entries must be >= 3 and clinic shares within 0..100')
    if any((a.f2_dead_retry,a.f2_joint_focus,a.f2_joint_model))and not a.f2_readiness_probes:
        p.error('--f2-dead-retry, --f2-joint-focus and --f2-joint-model require --f2-readiness-probes')
    if a.f2_joint_focus and a.focus_carry:p.error('--f2-joint-focus cannot be combined with --focus-carry')
    if a.paired_card_every<0:p.error('--paired-card-every must be >= 0')
    if a.paired_card_joint and not(a.f2_joint_model and a.f2_readiness_probes and a.paired_card_probes):
        p.error('--paired-card-joint requires --f2-joint-model, --f2-readiness-probes and --paired-card-probes')
    if a.f2_readiness_every<1:p.error('--f2-readiness-every must be positive')
    if a.beam is not None and a.beam<1:p.error('--beam must be positive')
    if a.normal_nodes is not None and a.normal_nodes<1:p.error('--normal-nodes must be positive')
    if not 0<=a.gate_retry_percent<=100:p.error('--gate-retry-percent must be within 0..100')
    root_policies=tuple(name for name in a.root_policies.split(',')if name)
    try:
        if resolve_policies(root_policies)!=root_policies:raise ValueError('duplicate or native entry')
    except ValueError as error:p.error('--root-policies: '+str(error))
    if root_policies and(a.prefix or a.root_portfolio!=1):p.error('--root-policies needs a fresh start and --root-portfolio 1')
    if a.focus_cluster_cap<0 or not 1<=a.focus_cluster_percent<=100:p.error('--focus-cluster-cap must be >= 0 and --focus-cluster-percent within 1..100')
    if not 0<=a.focus_optimism<=400:p.error('--focus-optimism must be within 0..400')
    if(a.focus_family or a.focus_optimism or a.focus_carry)and a.scheduler!='focus':p.error('--focus-family, --focus-optimism and --focus-carry need --scheduler focus')
    if a.focus_stall<0 or(a.focus_stall and a.scheduler!='focus'):p.error('--focus-stall must be >= 0 and needs --scheduler focus')
    if a.focus_stall_extended and (a.scheduler!='focus' or a.focus_stall<=0):p.error('--focus-stall-extended requires --scheduler focus and positive --focus-stall')
    if a.focus_stall_f2 and not a.focus_stall_extended:p.error('--focus-stall-f2 requires --focus-stall-extended')
    if a.requeue_lost<0:p.error('--requeue-lost must be >= 0')
    if a.root_jitter<0 or not 1<=a.jitter_percent<=100:p.error('--root-jitter must be >= 0 and --jitter-percent within 1..100')
    if a.root_jitter and(a.prefix or a.root_portfolio!=1):p.error('--root-jitter needs a fresh start and --root-portfolio 1')
    if a.root_async and a.root_portfolio!=1:p.error('--root-async needs --root-portfolio 1')
    if a.root_round<1:p.error('--root-round must be >= 1')
    gates=preset(a.gate_preset)
    if a.final_gate_plan!='none' and a.final_gate_plan not in gates.get('final',{}):p.error('--final-gate-plan: the gate preset has no such plan')
    try:probe_schedule=tuple(int(n)for n in a.gate_probe_schedule.split(',')if n)
    except ValueError:p.error('--gate-probe-schedule must be comma-separated integers')
    if any(n<1 for n in probe_schedule)or list(probe_schedule)!=sorted(set(probe_schedule)):p.error('--gate-probe-schedule must be increasing positive integers')
    if probe_schedule and(a.advisor!='combatsolver'or a.prefix or a.scheduler not in('focus','weighted')):
        p.error('--gate-probe-schedule needs the combatsolver advisor, a fresh start and the focus or weighted scheduler')
    if a.gate_probe_cards<1 or a.gate_probe_chunk<1:p.error('--gate-probe-cards and --gate-probe-chunk must be positive')
    if (a.f1_winner_reuse or a.real_card_menu_probes or a.gold_shop_routes or a.low_hp_routes
            or a.lean_third_act or a.shop_preparation or a.resource_telemetry or a.macro_routes!='off'):
        if (a.character!='IRONCLAD' or a.ascension!=10 or a.unlocks!='all' or a.advisor!='combatsolver'
                or a.prefix or a.root_portfolio!=1):
            p.error('experimental research switches need a fresh IRONCLAD A10 all-unlocks combatsolver start and --root-portfolio 1')
        if a.nodes is None or a.nodes<1:p.error('experimental research switches require a positive deterministic --nodes budget')
    if a.repair_mode=='gate' and not gates['retries']:p.error('--repair-mode gate needs a gate preset with retry plans')
    if (gates['plans'] or a.normal_nodes) and a.advisor!='combatsolver':p.error('gate plans need the combatsolver advisor')
    return p,a,gates,probe_schedule,root_policies


def search_config(a,gates,probe_schedule,root_policies):
    """SearchConfig of parsed planner arguments (shared by main and offline simulation)."""
    return SearchConfig(a.evaluations,a.seconds,a.task_seconds,a.max_decisions,a.lookahead_actions,
        a.lookahead_floors,a.alternatives,a.survivors,a.repair_window,scheduler=a.scheduler,dispatch_mode=a.dispatch,
        repair_mode=a.repair_mode,boss_repair_ms=a.boss_repair_ms,root_portfolio=a.root_portfolio,
        solver_seed=a.solver_seed,low_io=a.low_io,checkpoint_mib=a.checkpoint_mib,cache_mib=a.cache_mib,archive_entries=a.archive_entries,
        f2_readiness_probes=a.f2_readiness_probes,f2_dead_retry=a.f2_dead_retry,
        f2_joint_focus=a.f2_joint_focus,f2_joint_model=a.f2_joint_model,f2_readiness_every=a.f2_readiness_every,
        dispatch_window=a.dispatch_window,snapshot_stride=a.snapshot_stride,event_driven_settle=a.event_driven_settle,
        focus_elites=a.focus_elites,focus_pool=a.focus_pool,focus_share=a.focus_share,site_cap=a.site_cap,prior=a.prior,
        gate_retry_plans=tuple(gates['retries'])if a.repair_mode=='gate'else(),gate_retry_percent=a.gate_retry_percent,
        root_policies=root_policies,focus_cluster_cap=a.focus_cluster_cap,focus_cluster_percent=a.focus_cluster_percent,
        focus_family=a.focus_family,focus_optimism=a.focus_optimism,focus_carry=a.focus_carry,focus_stall=a.focus_stall,focus_stall_extended=a.focus_stall_extended,focus_stall_f2=a.focus_stall_f2,root_async=a.root_async,root_round=a.root_round,requeue_lost=a.requeue_lost,restart_jitter=a.restart_jitter,root_jitter=a.root_jitter,jitter_percent=a.jitter_percent,
        gate_probe_schedule=probe_schedule,gate_probe_cards=a.gate_probe_cards,gate_probe_chunk=a.gate_probe_chunk,
        gate_probe_plan=gates.get('probe')if a.gate_probe_plan=='light'else None,paired_card_probes=a.paired_card_probes,
        paired_card_every=a.paired_card_every,paired_card_dedup=a.paired_card_dedup,paired_card_merge=a.paired_card_merge,
        paired_card_joint=a.paired_card_joint,
        gate_timing=a.gate_timing,clinic=a.clinic,clinic_min_entries=a.clinic_min_entries,
        clinic_root_share=a.clinic_root_share,clinic_undecided_share=a.clinic_undecided_share,
        clinic_depth_share=a.clinic_depth_share,clinic_retry_share=a.clinic_retry_share,
        clinic_aux_share=a.clinic_aux_share,clinic_aux_contended=a.clinic_aux_contended,clinic_hints=a.clinic_hints,
        strategy_prior=a.strategy_prior,paired_card_first=a.paired_card_first,tail_mode=a.tail_mode,tail_sites=a.tail_sites,tail_mib=a.tail_mib,
        tail_cohort_inflight=a.tail_cohort_inflight,tail_feedback_rounds=a.tail_feedback_rounds,
        gate_model_minimum=a.gate_model_minimum,gate_model_refresh=a.gate_model_refresh,
        macro_plateau=a.macro_plateau,macro_widening=a.macro_widening,macro_fair=a.macro_fair,
        macro_routes=a.macro_routes,macro_aux_burst=a.macro_aux_burst,macro_route_limit=a.macro_route_limit,
        macro_route_every=a.macro_route_every,macro_route_paths=a.macro_route_paths,
        macro_route_expansions=a.macro_route_expansions,macro_route_queue_mib=a.macro_route_queue_mib,
        f1_winner_reuse=a.f1_winner_reuse,real_card_menu_probes=a.real_card_menu_probes,
        gold_shop_routes=a.gold_shop_routes,low_hp_routes=a.low_hp_routes,low_hp_routes_any_act=a.low_hp_routes_any_act,lean_third_act=a.lean_third_act,
        shop_preparation=a.shop_preparation,resource_telemetry=a.resource_telemetry,
        gold_shop_threshold=a.gold_shop_threshold,low_hp_percent=a.low_hp_percent,preparation_limit=a.preparation_limit,forge_menu_dedup=a.forge_menu_dedup,
        memory_telemetry=a.memory_telemetry,preserve_completed_prefix=a.preserve_completed_prefix,prefer_f1_hp=a.prefer_f1_hp)


def configure_advisor(advisor,a,gates,dop):
    """Apply the parsed budgets and gate plans to the combat advisor request data."""
    advisor.update(budget_ms=a.budget_ms,boss_budget_ms=a.boss_budget_ms,dop=dop,
                   profile=a.profile,reuse_continuations=not a.no_continuation_reuse,
                   complete_continuations_only=a.complete_continuations_only)
    if a.nodes:advisor['nodes']=a.nodes
    if a.beam is not None:advisor['beam']=a.beam
    if gates['plans']:advisor['gate_plans']=gates['plans']
    if a.final_gate_plan!='none':advisor['gate_plans']={**gates['plans'],'FinalBoss':gates['final'][a.final_gate_plan]}
    if a.normal_nodes is not None:advisor['normal_nodes']=a.normal_nodes
    return advisor


def main(argv=None):
    p,a,gates,probe_schedule,root_policies=parse_planner(argv)
    if a.out.exists() and any(a.out.iterdir()):p.error('output must be new/empty; never overwrite prior evidence')
    data=game_data(a.game_dir)
    resources=ResourcePlan.detect(a.workers,a.dop,a.worker_memory_mib,a.reserve_mib)
    if os.environ.get('SPIRE_REQUIRE_EXACT_WORKERS')=='1' and resources.workers!=a.workers:
        p.error('Requested workers cannot fit available resources; refusing to silently clamp the scaling experiment')
    cfg=search_config(a,gates,probe_schedule,root_policies)
    ctx=context(a.seed,a.character,a.ascension,a.unlocks);a.out.mkdir(parents=True,exist_ok=True)
    prefix=[]
    if a.prefix:
        doc=read_json(a.prefix);prefix=doc if isinstance(doc,list) else doc.get('history',doc.get('trace'))
        if not isinstance(prefix,list):p.error('prefix JSON has no history/trace list')
    advisor=None
    if a.advisor=='combatsolver':
        adapter=NativeCampaignBackend(ctx,a.out,data,'combatsolver');advisor=adapter.advisor
        configure_advisor(advisor,a,gates,resources.dop)
    checkpoints=[]
    for path in a.import_checkpoints:
        checkpoints.extend(path.rglob('map-*.json') if path.is_dir() else [path])
    with NativePool(data,a.out/'workers',resources,runtime_profile=a.runtime_profile,queue_policy=a.queue_policy,
                    memory_policy=a.worker_memory_policy,max_jobs=a.worker_max_jobs) as pool:
        report=solve(ctx,a.out,pool,cfg,advisor=advisor,initial_prefix=prefix,import_checkpoints=checkpoints,
            progress=lambda row:print(json.dumps(row,ensure_ascii=False),flush=True))
    print(json.dumps({'result':str(a.out/'result.json'),'status':report['status'],'lower_bound':report['lower_bound'],
                     'upper_bound':report['upper_bound'],'normal_game_verified':False},ensure_ascii=False))

if __name__=='__main__':main()
