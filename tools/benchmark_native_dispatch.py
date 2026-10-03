"""Paired real-DLL dispatch benchmark, identical captured requests and node caps.

Diagnostic stored prefixes are never supplied to a production planner. All game
results are compared as full bytes; no candidate is counted as a planning win.
"""
from pathlib import Path
import argparse,json,os,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[1]
FROZEN=ROOT/'experiments/frozen-i021'
SOURCE=FROZEN/'experiments/iteration-021/m0-long-dev00-s271828/seed-564940356'
OUT=ROOT/'experiments/iteration-022/native-dispatch-benchmark'

def component(mode,out):
    sys.path.insert(0,str(FROZEN))
    from spire_exact.planning.pool import NativePool
    from spire_exact.planning.resources import ResourcePlan
    from spire_exact.planning.io import read_json,write_json
    from spire_exact.canonical import canonical
    specs=[]
    for e in read_json(SOURCE/'result.json')['evaluations'][:28]:
        req=read_json(SOURCE/e['label']/'request.json')
        req['capture_checkpoints']=False;req['low_io']=True
        if req.get('checkpoint')and not Path(req['checkpoint']).is_file():raise RuntimeError('Diagnostic checkpoint missing; do not silently change work')
        req['advisor'].update(nodes=2000,budget_ms=600000,boss_budget_ms=600000)
        specs.append(req)
    data=FROZEN/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
    begun=time.perf_counter();rows=[]
    with NativePool(data,out/'workers',ResourcePlan.detect(7,1,640,1024))as pool:
        width=7 if mode=='batch'else len(specs)
        for start in range(0,len(specs),width):
            pending=[(i,pool.submit(req,out/f'case-{i:03}',900))for i,req in enumerate(specs[start:start+width],start)]
            for i,future in pending:
                result,identity=future.result()
                semantic={k:result.get(k)for k in ['status','phase','reason','value','observation','trace','decision_evidence','native_terminal_observed']}
                (out/f'semantic-{i:03}.json').write_bytes(canonical(semantic))
                rows.append({'index':i,'status':result['status'],'wall':time.perf_counter()-begun,
                             'node_count':sum(s.get('expanded_nodes')or 0 for s in (result.get('advisor_metrics')or{}).get('searches',[]))})
    write_json(out/'component.json',{'mode':mode,'requests':len(specs),'rows':rows,'wall_seconds':time.perf_counter()-begun,
        'counts_as_planner_win':False,'native_identity':identity,'node_budget':2000})

def main():
    p=argparse.ArgumentParser();p.add_argument('--child',choices=['batch','pipeline']);a=p.parse_args()
    if a.child:
        sys.path.insert(0,str(ROOT))
        from tools import limited_cli
        target=OUT/a.child
        sys.argv=[__file__,'--out',str(target)]
        # Reuse the same audited Windows Job wrapper for this diagnostic module.
        limited_cli.runpy.run_module=lambda *args,**kwargs:component(a.child,target)
        limited_cli.main();return
    OUT.mkdir(parents=True,exist_ok=False)
    env=dict(os.environ,SPIRE_PROTOCOL='A10-seed-v2',SPIRE_WALL_LIMIT_SECONDS='1800',PYTHONIOENCODING='utf-8')
    for mode in ['batch','pipeline']:
        with (OUT/(mode+'.log')).open('wb')as log:
            subprocess.run([sys.executable,__file__,'--child',mode],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
    rows={m:json.loads((OUT/(m+'-resources.json')).read_text())for m in ['batch','pipeline']}
    files=list((OUT/'batch').glob('semantic-*.json'))
    same=bool(files)and all(p.read_bytes()==(OUT/'pipeline'/p.name).read_bytes()for p in files)
    report={'full_game_evidence_equal':same,'cases':len(files),'counts_as_planner_win':False,
            'arms':{m:{'wall_seconds':r['wall_seconds'],'cpu_seconds':r['job_cpu_seconds'],
                'average_busy_cores':r['job_cpu_seconds']/r['wall_seconds'],'peak_commit_bytes':r['peak_job_commit_bytes']}for m,r in rows.items()}}
    (OUT/'report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)
    if not same:raise SystemExit('Native semantic mismatch; no promotion')

if __name__=='__main__':main()
