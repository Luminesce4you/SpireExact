"""Counterbalanced same-seed/same-CPU repair-policy ablation, development only."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
from threading import Lock
import os,sys,subprocess,json
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.planning.io import read_json,write_json
FROZEN=ROOT/'experiments/frozen-i015'
out=ROOT/'experiments/iteration-015/paired-repairs';out.mkdir(parents=True,exist_ok=False)
seeds=['0','1','2','43'];policies=['fifo','deep_final_boss'];rows=[];lock=Lock()
write_json(out/'manifest.json',{'workspace':str(FROZEN),'freeze':read_json(FROZEN/'freeze.json'),
    'seeds':seeds,'kind':'development_smoke','per_seed_seconds':180,'per_seed_cpus':8,
    'orders':{s:policies if i%2==0 else policies[::-1] for i,s in enumerate(seeds)},
    'cpu_offsets':{s:8*i for i,s in enumerate(seeds)},'fresh_validation_evidence':False})

def lane(index,seed):
    env=dict(os.environ,SPIRE_CPU_OFFSET=str(8*index),SPIRE_TARGET_CPUS='8',SPIRE_MEMORY_BUDGET_MIB='6144')
    for policy in policies if index%2==0 else policies[::-1]:
        run_id=f'i015-paired-{policy}-seed-{seed}'
        cmd=[sys.executable,str(FROZEN/'tools/experiment.py'),'--iteration','iteration-015',
             '--run-id',run_id,'--kind','smoke','--count','1','--dev-seeds',seed,'--full-rollout','--repair-mode',policy]
        print(json.dumps({'event':'start','seed':seed,'policy':policy,'cpu_offset':8*index}),flush=True)
        with (out/f'{seed}-{policy}.log').open('w',encoding='utf-8')as log:
            rc=subprocess.run(cmd,cwd=FROZEN,env=env,stdout=log,stderr=subprocess.STDOUT).returncode
        result=FROZEN/'experiments/iteration-015'/run_id/'results.json'
        row={'seed':seed,'policy':policy,'cpu_offset':8*index,'exit_code':rc,'result':str(result)}
        if result.exists():row.update(read_json(result)[0])
        else:row.update(win=False,reason='EXPERIMENT_INCOMPLETE')
        with lock:
            rows.append(row);write_json(out/'results.json',rows)
            print(json.dumps({'event':'result','seed':seed,'policy':policy,'win':row['win'],
                              'reason':row['reason'],'floor':row.get('max_floor')}),flush=True)
with ThreadPoolExecutor(max_workers=4)as executor:
    for future in as_completed([executor.submit(lane,i,s)for i,s in enumerate(seeds)]):future.result()
summary={p:{'tested':sum(r['policy']==p for r in rows),'wins':sum(r['policy']==p and r['win']for r in rows),
            'floors':{r['seed']:r.get('max_floor')for r in rows if r['policy']==p},
            'all_complete':all(r.get('reason')!='EXPERIMENT_INCOMPLETE'for r in rows if r['policy']==p)}for p in policies}
write_json(out/'summary.json',summary);print(json.dumps(summary),flush=True)
