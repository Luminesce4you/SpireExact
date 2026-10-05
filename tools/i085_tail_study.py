"""Deterministic counterexamples and service microstudy, NOT STS2 rollouts.

Every oracle result below is fabricated, marked fixture_only, and always a
route death. `toy_goal` is an external predicate, NEVER a native win candidate.
Simulated costs permit cost-allocation experiments without inventing timings.
"""
from __future__ import annotations
import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import random
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from spire_exact.canonical import canonical
from spire_exact.planning.focus import FocusScheduler
from spire_exact.planning.tail_search import TailFocusScheduler,ServiceClock
from tools.benchmark_i085 import source_hash
from tools.tail_statistics import restart_expectation, survival_summary

WORLDS=('early_pair','late_single','late_pair','misleading_late_progress','unsolved')


def oracle(history, world, seed, menus=15):
    """Complete fixed-prefix replay, baseline action zero for new suffix menus."""
    rng=random.Random(seed)
    target=[rng.choice((1,2)) for _ in range(menus)]
    # Cover all four combinations instead of accidental repeated RNG targets.
    targets=(1+(seed%2),1+((seed//2)%2))
    indices=(0,1) if world=='early_pair' else (menus-2,menus-1) if world=='late_pair' else (0,2)
    for i,v in zip(indices,targets):target[i]=v
    if world=='late_single':target[-1]=targets[0]
    trace=[];evidence=[];choices=[];deck=[]
    for i in range(menus):
        act=i//5;floor=i*3+1
        options=[{'kind':'card_reward','index':j,'card':f'X{i}_{j}'} for j in range(3)]
        choice=history[i] if i<len(history) else options[0]
        if choice not in options:raise ValueError('fixture prefix not legal')
        obs={'act':act,'floor':floor,'hp':'60','max_hp':'80','gold':0,
             'deck':deepcopy(deck),'potions':[None],'relics':[]}
        evidence.append({'phase':'card_reward','observation':obs,'available_actions':options,
                         'option_labels':[['card:'+a['card']] for a in options]})
        trace.append(choice);choices.append(choice['index'])
        deck.append({'id':choice['card'],'upgrade':0})
    if world=='early_pair':goal=choices[0]==target[0] and choices[1]==target[1]
    elif world=='late_single':goal=choices[-1]==target[-1]
    elif world=='late_pair':goal=choices[-2:]==target[-2:]
    elif world=='misleading_late_progress':goal=choices[0]==target[0] and choices[2]==target[2]
    elif world=='unsolved':goal=False
    else:raise ValueError(world)
    # Make real gate helpers see a known F1 pass and known F2 loss. This is
    # still fabricated input; it does not run the game nor create certificates.
    enemy=lambda hp:{'id':'FIXTURE_BOSS','combat_id':1,'hp':str(hp),'max_hp':'100'}
    common={'act':2,'hp':'60','max_hp':'80','deck':deck,'potions':[None],'relics':[]}
    for action,phase,obs in [({'kind':'end_turn'},'combat',dict(common,floor=46,room='Boss',turn=1,enemies=[enemy(100)])),
                            ({'kind':'reward','index':0},'rewards',dict(common,floor=46,room=None)),
                            ({'kind':'end_turn'},'combat',dict(common,floor=47,room='Boss',turn=1,enemies=[enemy(100)]))]:
        trace.append(action);evidence.append({'phase':phase,'observation':obs,'available_actions':[action]})
    if canonical(trace[:len(history)])!=canonical(history):raise AssertionError('immutable prefix escaped')
    # High damage depends on tempting late choices, not on early complementary
    # ingredients; no trial can infer a feasibility bound from this surrogate.
    damage=sum(choices[i]==target[i] for i in range(10,menus))*15
    hp=max(1,99-damage)
    simulated_cost=1+0.1*(menus-len(choices[:len(history)]))
    if world=='misleading_late_progress':simulated_cost+=8*(choices[-1]!=0)
    result={'status':'TERMINAL','value':[0],'trace':trace,'decision_evidence':evidence,
            'observation':dict(common,floor=47,room='Boss',hp='0'),
            'terminal_combat':dict(common,floor=47,room='Boss',turn=5,hp='0',enemies=[enemy(hp)]),
            'campaign':{'act_count':3,'final_act_boss_count':2},
            'performance':{'wall_us':round(simulated_cost*1e6)},
            'fixture_only':True,'toy_goal':goal,'native_terminal_observed':False}
    return result,simulated_cost


def experiment(world,seed,arm,window,budget=120,seconds=None):
    scheduler=(FocusScheduler() if arm=='i082-focus-core' else
               FocusScheduler(plateau_diversity=True,recover_deferred=True) if arm=='i085-macro-core' else
               TailFocusScheduler(plateau_diversity=True,recover_deferred=True,solver_seed=seed))
    root,cost=oracle([],world,seed);scheduler.add(root,'root')
    pending=[];events=[];submitted=0;clock=0.;work=cost;known=cost;goal=None;goal_work=None
    # 7 identical abstract worker lanes, FIFO execution, ordered absorption.
    # This models feedback backlog, not the actual NativePool implementation.
    lanes=[cost]*min(7,window)
    python_start=time.perf_counter()
    budget_end=float('inf') if seconds is None else float(seconds)
    while (submitted<budget or pending) and clock<budget_end:
        while submitted<budget and len(pending)<window and clock<budget_end:
            group=scheduler.next()
            if group is None:break
            result,cost=oracle(group['prefix'],world,seed)
            lane=min(range(len(lanes)),key=lambda i:lanes[i]);finish=max(clock,lanes[lane])+cost
            lanes[lane]=finish;submitted+=1;work+=cost
            pending.append((group,result,cost,finish,submitted))
        if not pending:break
        if max(clock,pending[0][3])>budget_end:
            clock=budget_end;break  # not-yet-observed successes are censored
        group,result,cost,finish,number=pending.pop(0);clock=max(clock,finish);known+=cost
        spec={'group':group}
        if isinstance(scheduler,TailFocusScheduler):
            scheduler.add(result,str(number),spec,completed_wall_seconds=clock)
        else:scheduler.add(result,str(number))
        events.append({'proposal':number,'act':group.get('act',group['diagnosis'].get('act')),
                       'decision_index':group['index'],'category':group['category'],
                       'simulated_job_seconds':cost,'observed_simulated_seconds':clock,
                       'toy_goal':result['toy_goal']})
        if result['toy_goal']:
            goal=clock;goal_work=known;break
    return {'world':world,'seed':seed,'arm':arm,'dispatch_window':window,'evaluation_cap':budget,
            'simulated_time_cap':seconds,'goal_found':goal is not None,'time_to_toy_goal_simulated':goal,
            'first_goal_absorbed_evaluation':len(events) if goal is not None else None,
            'simulated_observed_worker_work_at_goal':goal_work,
            'simulated_observed_seconds':clock,'simulated_submitted_worker_work':work,
            'pending_at_stop':len(pending),'actual_python_seconds':time.perf_counter()-python_start,
            'events':events,'snapshot':scheduler.snapshot(),'native_executed':False,
            'scope':'fabricated deterministic world; simulation time != native time; no verified native win'}


def study(budget=120,seeds=(1,2,3,4),seconds=None):
    trials=[experiment(world,seed,arm,window,budget,seconds)
            for world in WORLDS for seed in seeds for window in (1,14)
            for arm in ('i082-focus-core','i085-macro-core','i085-tail')]
    summary=[]
    for world in WORLDS:
        for window in (1,14):
            for arm in ('i082-focus-core','i085-macro-core','i085-tail'):
                rows=[r for r in trials if (r['world'],r['dispatch_window'],r['arm'])==(world,window,arm)]
                # Finite matched evaluation cap, not equal wall-time inference.
                summary.append({'world':world,'window':window,'arm':arm,'found':sum(r['goal_found'] for r in rows),
                    'cases':len(rows),'goal_evaluations':[r['first_goal_absorbed_evaluation'] for r in rows],
                    'restricted_evaluation_mean':sum(r['first_goal_absorbed_evaluation'] or budget for r in rows)/len(rows),
                    'python_seconds':sum(r['actual_python_seconds'] for r in rows),
                    'restricted_simulated_time_mean':sum(r['time_to_toy_goal_simulated'] if r['goal_found'] else seconds for r in rows)/len(rows) if seconds else None})
    clock=ServiceClock();persistent=0;zero_debt=0
    # Retiring newborn each iteration is the explicit adversarial arrival model.
    tags={'old':1.}
    for i in range(60):
        k=clock.choose(['old',f'new{i}']);persistent+=k=='old';clock.charge(k,1)
        tags[f'new{i}']=0.;winner=min(('old',f'new{i}'),key=lambda k:tags[k]);zero_debt+=winner=='old';tags[winner]+=1
    return {'schema':'spire-i085-tail-constructed/v2','source_hash':source_hash(),'native_executed':False,
            'trials':trials,'summary':summary,'newborn_counterexample':{'rounds':60,'persistent_old_picks':persistent,'zero_debt_old_picks':zero_debt},
            'restart_math':{'distribution':[[1,.1],[100,.9]],'no_restart_mean':90.1,
                 'cutoff_1_no_overhead':restart_expectation([(1,.1),(100,.9)],1),
                 'cutoff_1_overhead_20':restart_expectation([(1,.1),(100,.9)],1,20),
                 'deterministic_same_run_cutoff_10':restart_expectation([(100,1)],10)},
            'limits':'Predeclared constructed worlds, four seeds, two feedback windows; not randomized native benchmarks, '
                     'not a fitted heavy-tail distribution, not general speedup evidence. All outcomes, including regressions, retained.'}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True,type=Path)
    p.add_argument('--evaluations',type=int,default=120);p.add_argument('--simulated-seconds',type=float);a=p.parse_args(argv)
    if a.out.exists() or a.evaluations<1:p.error('new output and positive budget required')
    if a.simulated_seconds is not None and a.simulated_seconds<=0:p.error('positive simulated time required')
    result=study(a.evaluations,seconds=a.simulated_seconds);a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'trials':len(result['trials']),'native_executed':False,'out':str(a.out)}))

if __name__=='__main__':main()
