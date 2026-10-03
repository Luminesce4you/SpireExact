"""Recover actual proposal ancestry and diagnostic outcomes from a completed run."""
from pathlib import Path
import argparse, collections, json, sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.planning.io import read_json,write_json
from spire_exact.canonical import canonical

def audit(source,out):
    result=read_json(source/'result.json');records=result['evaluations']
    by_label={r['label']:r for r in records}
    macros=sorted([r for r in records if r['kind'].startswith('macro_')],key=lambda r:int(r['label'].split('-')[1]))
    groups=result['lookahead_groups']
    if len(macros)!=len(groups):raise ValueError('Cannot infer proposal mapping: unequal generated and recorded macro counts')
    group_map={r['label']:g for r,g in zip(macros,groups)}
    lineage=[];label=result['best_label'];visited=set()
    while label in group_map:
        if label in visited:raise ValueError('Cyclic ancestry')
        visited.add(label);group=group_map[label];parent=group['source'];index=group['index']
        request=read_json(source/label/'request.json')
        previous=read_json(source/parent/'data/decision.json')
        if not canonical(request['history'][:index])==canonical(previous['trace'][:index]):
            raise ValueError('Proposal order mapping failed full prefix verification')
        if len(request['history'])!=index+1:raise ValueError('Expected one strategic deviation')
        r=by_label[label];pr=by_label[parent]
        lineage.append({'child':label,'parent':parent,'decision_index':index,'floor':group['floor'],
            'phase':group['phase'],'category':group['category'],
            'old_action':previous['trace'][index],'new_action':request['history'][index],
            'parent_terminal_floor':(pr.get('observation')or{}).get('floor'),
            'child_terminal_floor':(r.get('observation')or{}).get('floor'),
            'parent_classification':pr['classification'],'child_classification':r['classification'],
            'child_wall_seconds':r['completed_wall_seconds']})
        label=parent
    deaths=collections.Counter();by_kind={};failed_nodes=[]
    for r in records:
        by_kind.setdefault(r['kind'],collections.Counter())[r['classification']]+=1
        if r['classification']=='NATIVE_ROUTE_DEATH':
            o=r.get('observation')or{};deaths[f"act{o.get('act',-1)+1}/floor{o.get('floor')}/{o.get('room')}"]+=1
        for s in (r.get('advisor_metrics')or{}).get('searches',[]):
            if s.get('only_death_routes_found'):failed_nodes.append(s['expanded_nodes'])
    report={'source':str(source.resolve()),'ancestry':list(reversed(lineage)),'ancestry_root':label,
            'prefix_relationships_full_bytes_verified':True,'death_locations':dict(deaths.most_common()),
            'outcomes_by_kind':{k:dict(v)for k,v in by_kind.items()},
            'only_death_forecasts':{'count':len(failed_nodes),'median_expanded_nodes':sorted(failed_nodes)[len(failed_nodes)//2] if failed_nodes else None,
                                   'max_expanded_nodes':max(failed_nodes,default=None)},
            'causal_limitation':'Ancestry shows executed interventions, not isolated causal effects or seed infeasibility.'}
    write_json(out/'lineage.json',report)
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();audit(a.source,a.out)
