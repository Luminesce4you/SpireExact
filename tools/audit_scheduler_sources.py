"""Inspect allocation after a seed has produced a final-boss candidate."""
from pathlib import Path
from collections import Counter
import json
ROOT=Path(__file__).resolve().parents[1]
base=ROOT/'experiments/frozen-i007/experiments/iteration-007/i007-fresh20-v2'
rows=[]
for seed in json.loads((base/'results.json').read_text()):
    result=json.loads(Path(seed['result']).read_text());evaluations=result['evaluations']
    bylabel={e['label']:e for e in evaluations}
    final=[e for e in evaluations if e['classification']=='NATIVE_ROUTE_DEATH'
           and (e.get('observation')or{}).get('floor')==48]
    if not final:continue
    first=min(int(e['label'].split('-')[1]) for e in final)
    # Lookahead group labels are recorded as their source, with no submission id;
    # only sources generated at/after the first final-boss candidate are certain.
    counts=Counter();examples=[]
    for group in result.get('lookahead_groups',[]):
        source=bylabel.get(group.get('source'))
        if source is None:continue
        floor=(source.get('observation')or{}).get('floor',0)
        counts[str(floor)]+=1
        if int(source['label'].split('-')[1])>=first:
            examples.append({'source':source['label'],'source_floor':floor,'branch_floor':group['floor'],
                             'category':group['category']})
    rows.append({'seed':seed['seed'],'won':seed['win'],'first_final_boss_evaluation':first,
                 'total_evaluations':len(evaluations),'all_source_floors':dict(counts),
                 'branches_from_sources_created_after_final_boss_discovery':examples})
out=ROOT/'experiments/iteration-012/scheduler-source-audit.json'
out.write_text(json.dumps(rows,indent=2),encoding='utf-8')
for r in rows:print(r['seed'],r['won'],r['first_final_boss_evaluation'],r['total_evaluations'],r['all_source_floors'])
