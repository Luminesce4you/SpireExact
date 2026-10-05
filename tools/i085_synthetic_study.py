"""Mechanism counterexamples and coordinator microcosts, NOT a native benchmark."""
from __future__ import annotations
import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import statistics
import sys
from time import perf_counter

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from spire_exact.planning.indexed_strategy import IndexedStrategicScheduler
from spire_exact.planning.preparation import NativeMap
from spire_exact.planning.macro_i085 import enumerate_routes, representative_routes
from tools.benchmark_i085 import source_hash
from test_i085_macro import WideningTests
from test_focus_search import run


def layered_map(width=5,rows=14):
    point=lambda c,r:{'col':c,'row':r}
    nodes=[{**point(c,r),'type':('Monster','Elite','Shop','RestSite','Event')[(c+r)%5]}
            for r in range(rows) for c in range(width)]
    boss=point(0,rows);nodes.append({**boss,'type':'Boss'})
    edges=[{'from':point(c,r),'to':point(d,r+1)} for r in range(rows-1)
           for c in range(width) for d in sorted({c,(c+1)%width})]
    edges += [{'from':point(c,rows-1),'to':boss} for c in range(width)]
    graph={'schema':'spire-current-act-map/v1','act':0,'nodes':nodes,'edges':edges,'boss_coord':boss}
    return NativeMap(graph,[{'kind':'map',**point(c,0)} for c in range(width)])


def study():
    coverage=[]
    for alternatives in (30,127,511):
        values=[]
        for recovery in (False,True):
            scheduler=IndexedStrategicScheduler(site_cap=12,recover_deferred=recovery)
            start=perf_counter();scheduler.add(WideningTests.menu(alternatives+1),'fixture')
            seen=[]
            while (candidate:=scheduler.next()) is not None:seen.append(candidate['prefix'][-1]['index'])
            values.append({'recovery':recovery,'scheduled_distinct_options':len(set(seen)),
                'unreached_fixture_options':alternatives-len(set(seen)),
                'coordinator_seconds':perf_counter()-start,'snapshot':scheduler.snapshot()})
        coverage.append({'available_alternatives':alternatives,'results':values})
    toy=[]
    for budget in (20,40,80):
        for enabled in (False,True):
            result,requests=run(evaluations=budget,macro_plateau=enabled,macro_widening=enabled,macro_fair=enabled)
            toy.append({'budget':budget,'macro_i085':enabled,'best_floor':result['best_observation']['floor'],
                'scheduled_by_kind':dict(Counter(kind for kind,_ in requests)),
                'status':result['status'],'verified_native_wins':0})
    graph=layered_map();timings=[];stats=None;chosen=None
    for _ in range(10):
        start=perf_counter();paths,stats=enumerate_routes(graph,max_paths=256,max_expansions=4096)
        chosen=representative_routes(graph,paths,paths[0],limit=3)
        timings.append(perf_counter()-start)
    return {'schema':'spire-i085-synthetic-analysis/v1','source_hash':source_hash(),'native_executed':False,
        'menu_coverage_counterexample':coverage,'deterministic_toy_campaign':toy,
        'structural_route_microcost':{'nodes':len(graph.nodes),'edges':sum(map(len,graph.edges.values())),
            'complete_paths_considered':stats['complete_candidate_paths'],'enumeration':stats,
            'selected':len(chosen),'repetitions':len(timings),'median_seconds':statistics.median(timings),
            'maximum_seconds':max(timings),'measured_seconds':timings},
        'interpretation':'Menu coverage tests a scheduler mechanism. The toy campaign never produces a native win. '
            'Route timings exclude native snapshots, validation, serialization, startup, replay and combat. '
            'These measurements do not predict game win rate or end-to-end speedup.'}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True,type=Path);a=p.parse_args(argv)
    if a.out.exists():p.error('preserve previous evidence; use a fresh path')
    value=study();a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'native_executed':False,'report':str(a.out),'route_microcost_median':value['structural_route_microcost']['median_seconds']}))


if __name__=='__main__':main()
