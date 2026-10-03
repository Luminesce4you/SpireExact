from pathlib import Path
import json,statistics
ROOT=Path(__file__).resolve().parents[1]
catalog=json.loads((ROOT/'experiments/iteration-011/native-catalog/data/catalog.json').read_text(encoding='utf-8'))
types={c['id']:c['type'] for c in catalog['cards']}
base=ROOT/'experiments/frozen-i007/experiments/iteration-007/i007-fresh20-v2'
rows=[]
for sample in json.loads((base/'results.json').read_text()):
    p=Path(sample['result']);r=json.loads(p.read_text());candidates=[]
    for ev in r['evaluations']:
        if ev['classification'] not in ('NATIVE_ROUTE_DEATH','NATIVE_WIN_CANDIDATE'):continue
        if (ev.get('observation')or{}).get('floor')!=48:continue
        file=p.parent/ev['label']/'data/decision-evidence.jsonl'
        if not file.exists():continue
        with file.open(encoding='utf-8') as f:
            for line in f:
                if not line.startswith('{"phase":"combat"'):continue
                obs=json.loads(line)['observation']
                if obs['floor']==48:
                    candidates.append((ev,obs));break
    if not candidates:continue
    if sample['win']:
        candidate=next((c for c in candidates if c[0]['classification']=='NATIVE_WIN_CANDIDATE'),candidates[0])
    else:candidate=max(candidates,key=lambda c:float(c[1]['hp']))
    ev,obs=candidate;deck=obs['deck'];powers=[c['id'] for c in deck if types.get(c['id'])=='Power']
    rows.append({'seed':sample['seed'],'won':sample['win'],'source':ev['label'],
        'boss':[e['id']for e in obs.get('enemies',[])],'entry_hp':obs['hp'],'max_hp':obs['max_hp'],
        'opening_energy':obs['energy'],'deck_size':len(deck),'power_cards':powers,
        'upgrades':sum(c['upgrade']>0 for c in deck),'strategic':obs['strategic'],
        'deck':deck,'relics':obs['relics'],'potions':obs['potions']})
summary={}
for won in (True,False):
    group=[r for r in rows if r['won']==won]
    summary[str(won)]={'games':len(group),'mean_deck_size':statistics.mean(r['deck_size']for r in group),
        'mean_power_count':statistics.mean(len(r['power_cards'])for r in group),
        'mean_opening_energy':statistics.mean(float(r['opening_energy'])for r in group),
        'mean_upgrade_count':statistics.mean(r['upgrades']for r in group)}
(ROOT/'experiments/iteration-011/final-deck-audit.json').write_text(json.dumps({'summary':summary,'rows':rows},indent=2))
print(json.dumps(summary,indent=2))
for r in rows:print(r['seed'],r['won'],r['boss'],'powers',r['power_cards'],'energy',r['opening_energy'])
