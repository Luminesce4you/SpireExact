"""Read-only live HTTP check of every stored winning decision; no browser automation."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dashboard.winning_route import WinningRouteStore,browser_values
from tools.fight_bench import _affinity


def fetch(path):
    with urlopen('http://127.0.0.1:8765'+path,timeout=30)as response:return response.read()


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    if a.out.exists():raise SystemExit('Fresh report required')
    _affinity('e')
    paths=[ROOT/'experiments/iteration-037-runs/focus-180-a/validation-manifest.json',
           ROOT/'experiments/iteration-manual/manual-20261001-190138-247fef/validation-manifest.json']
    store=WinningRouteStore();runs=[]
    for path in paths:
        expected=store.load(path);query=urlencode({'run':expected['run_id']});endpoint='/api/winning-route?'+query
        meta=json.loads(fetch(endpoint));checks={'verified':meta['verified'] is True,'total':meta['total_steps']==len(expected['_trace']),
             'all_summaries':len(meta['steps'])==len(expected['_trace'])}
        failed=[]
        for index,action in enumerate(expected['_trace']):
            step=json.loads(fetch(endpoint+'&step='+str(index)))
            canonical_step=store.step(expected,index)
            if step!=canonical_step or step['action']!=browser_values(action):failed.append(index)
        exported=json.loads(fetch(endpoint+'&export=1'))
        checks['every_step_exact']=not failed
        checks['untruncated_export']=exported['trace']==expected['_trace'] and exported['decision_evidence']==expected['_evidence']
        runs.append({'run':expected['run_id'],'steps':len(expected['_trace']),'checks':checks,'failed_step_indices':failed})
    assets={}
    for name in ('index.html','app.js','route.js','route.css'):
        payload=fetch('/'+name)
        assets[name]={'served_bytes_match':payload==(ROOT/'dashboard/web'/name).read_bytes(),'sha256':hashlib.sha256(payload).hexdigest()}
    report={'passed':all(all(r['checks'].values())for r in runs)and all(v['served_bytes_match']for v in assets.values()),
            'runs':runs,'assets':assets,'server':json.loads(fetch('/api/health')),
            'scope':'real local HTTP, all decisions and raw exports; browser visual layout not inspected'}
    a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
