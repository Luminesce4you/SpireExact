"""Fresh searches on prior winning regression seeds; NEVER import their routes."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
from threading import Lock
import os,sys,subprocess,json
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.planning.io import read_json,write_json
out=ROOT/'experiments/iteration-018/paired-versions';out.mkdir(parents=True,exist_ok=False)
baseline=ROOT/'experiments/frozen-i007'
seeds=[r['seed']for r in read_json(baseline/'experiments/iteration-007/i007-fresh20-v2/results.json')if r['win']][:4]
arms=[('best_i007','frozen-i007',[]),('current_fifo','frozen-i018',[]),
      ('current_final','frozen-i018',['--repair-mode','deep_final_boss'])]
rows=[];lock=Lock()
write_json(out/'manifest.json',{'kind':'regression_comparison','seeds':seeds,'arms':arms,
    'orders':{s:[a[0]for a in arms[i%3:]+arms[:i%3]]for i,s in enumerate(seeds)},
    'cpu_offsets':{s:8*i for i,s in enumerate(seeds)},'per_seed_seconds':180,
    'seed_selection':'first4 certificate-matched winners in prior i007 manifest order',
    'old_routes_or_prefixes_passed_to_planner':False,'counts_as_fresh_validation':False})
def lane(index,seed):
    env=dict(os.environ,SPIRE_CPU_OFFSET=str(8*index),SPIRE_TARGET_CPUS='8',SPIRE_MEMORY_BUDGET_MIB='6144')
    for label,workspace,extra in arms[index%3:]+arms[:index%3]:
        frozen=ROOT/'experiments'/workspace;run_id=f'i018-{label}-seed-{seed}'
        cmd=[sys.executable,str(frozen/'tools/experiment.py'),'--iteration','iteration-018','--run-id',run_id,
             '--kind','smoke','--count','1','--dev-seeds',seed,'--full-rollout',*extra]
        print(json.dumps({'event':'start','seed':seed,'arm':label,'cpu_offset':index*8}),flush=True)
        with (out/f'{seed}-{label}.log').open('w',encoding='utf-8')as log:
            rc=subprocess.run(cmd,cwd=frozen,env=env,stdout=log,stderr=subprocess.STDOUT).returncode
        result=frozen/'experiments/iteration-018'/run_id/'results.json'
        row={'seed':seed,'arm':label,'cpu_offset':index*8,'exit_code':rc}
        if result.exists():row.update(read_json(result)[0])
        else:row.update(win=False,reason='EXPERIMENT_INCOMPLETE')
        with lock:
            rows.append(row);write_json(out/'results.json',rows)
            print(json.dumps({'event':'result','seed':seed,'arm':label,'win':row['win'],'reason':row['reason'],'floor':row.get('max_floor')}),flush=True)
with ThreadPoolExecutor(max_workers=4)as executor:
    for f in as_completed([executor.submit(lane,i,s)for i,s in enumerate(seeds)]):f.result()
summary={arm:{'wins':sum(r['arm']==arm and r['win']for r in rows),'tested':sum(r['arm']==arm for r in rows),
              'all_complete':all(r.get('reason')!='EXPERIMENT_INCOMPLETE'for r in rows if r['arm']==arm)}for arm,_,_ in arms}
write_json(out/'summary.json',summary);print(json.dumps(summary),flush=True)
