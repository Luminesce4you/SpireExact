"""Replay identical native diagnostic records through old/new allocators.

This does not execute a game or count any stored route as a planning win.
"""
from pathlib import Path
import argparse,json,subprocess,sys,time,hashlib
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.planning.io import read_json,write_json
from spire_exact.planning.strategy import StrategicScheduler
from spire_exact.planning.indexed_strategy import IndexedStrategicScheduler
from spire_exact.canonical import canonical

SOURCE=ROOT/'experiments/frozen-i021/experiments/iteration-021/m0-long-dev00-s271828/seed-564940356'
OUT=ROOT/'experiments/iteration-022/scheduler-benchmark'

def run(kind):
    from dashboard.server import ProcessSampler
    import os
    s=StrategicScheduler()if kind=='old'else IndexedStrategicScheduler();start=time.perf_counter();outputs=[]
    records=read_json(SOURCE/'result.json')['evaluations'];peak=0;sampler=ProcessSampler()
    for e in records[:96]:
        p=SOURCE/e['label']/'data/decision.json'
        if not p.exists()and not Path(str(p)+'.gz').exists():continue
        result=read_json(p);s.add(result,e['label'])
        for _ in range(2):
            proposal=s.next()
            if proposal is not None:outputs.append(canonical(proposal).decode())
        del result
        peak=max(peak,sampler.sample(os.getpid(),kind).get('private_bytes',0))
    elapsed=time.perf_counter()-start
    write_json(OUT/(kind+'-proposals.json'),outputs)
    write_json(OUT/(kind+'.json'),{'seconds':elapsed,'peak_sampled_private_bytes':peak,'proposals':len(outputs),'pending':len(s.branches),'kind':kind})

def main():
    p=argparse.ArgumentParser();p.add_argument('--child',choices=['old','new']);a=p.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    if a.child:run(a.child);return
    for kind in ['old','new']:
        subprocess.run([sys.executable,__file__,'--child',kind],cwd=ROOT,check=True)
    old=read_json(OUT/'old.json');new=read_json(OUT/'new.json')
    same=(OUT/'old-proposals.json').read_bytes()==(OUT/'new-proposals.json').read_bytes()
    report={'same_complete_proposals':same,'old':old,'new':new,'speedup':old['seconds']/new['seconds'],
            'memory_reduction':1-new['peak_sampled_private_bytes']/old['peak_sampled_private_bytes'],'native_games_run':0}
    write_json(OUT/'report.json',report);print(json.dumps(report),flush=True)
    if not same:raise SystemExit('Ordering regression: do not integrate')

if __name__=='__main__':main()
