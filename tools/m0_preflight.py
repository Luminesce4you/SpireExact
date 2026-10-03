"""Count-limited A10 reproducibility and compressed-checkpoint replay gate."""
from pathlib import Path
import argparse,json,os,subprocess,sys,time,gzip
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.planning.io import read_json,write_json
from spire_exact.canonical import canonical
from spire_exact.native import build_host
from spire_exact.planning.pool import NativePool,RUNTIME_PROFILES
from spire_exact.planning.resources import ResourcePlan
from tools.rolling_storage import logical_bytes,active_seed,RollingConsole,owner_root

def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,default=ROOT/'experiments/iteration-021/m0-preflight')
    p.add_argument('--dispatch',choices=['batch','ordered'],default='batch');p.add_argument('--evaluations',type=int,default=2)
    p.add_argument('--workers',type=int,default=2);p.add_argument('--dispatch-window',type=int,default=7)
    p.add_argument('--event-driven-settle',action='store_true')
    p.add_argument('--runtime-profile',choices=RUNTIME_PROFILES,default='legacy')
    p.add_argument('--queue-policy',choices=['fifo','short-prefix-first'],default='fifo')
    p.add_argument('--seeds',type=int,default=2);p.add_argument('--max-decisions',type=int,default=80)
    p.add_argument('--nodes',type=int,default=256);a=p.parse_args()
    out=a.out.resolve();out.mkdir(parents=True,exist_ok=False)
    # Compilation is setup, not solver throughput. Keep its I/O report separate
    # and warm the immutable host before creating any measured Windows Job.
    _,_,build=build_host(ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64')
    write_json(out/'native-build.json',build)
    panel=read_json(owner_root(ROOT)/'experiments/panels/A10-seed-v2/dev.json')
    rows=[];checks={};base_env=dict(os.environ,SPIRE_PROTOCOL='A10-seed-v2',SPIRE_WALL_LIMIT_SECONDS='900')
    for seed in panel['seeds'][:a.seeds]:
        evidence=[]
        for repeat in range(2):
            target=out/f'seed-{seed}-repeat-{repeat}'
            cmd=[sys.executable,str(ROOT/'tools/limited_cli.py'),'solve-p5','--seed',seed,'--ascension','10',
                 '--unlocks','all','--out',str(target),'--game-dir',str(ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'),
                 '--workers',str(a.workers),'--dop','1','--evaluations',str(a.evaluations),'--seconds','840','--task-seconds','600',
                 '--max-decisions',str(a.max_decisions),'--lookahead-actions',str(a.max_decisions),'--lookahead-floors','99',
                 '--budget-ms','600000','--boss-budget-ms','600000','--nodes',str(a.nodes),'--dispatch',a.dispatch,'--dispatch-window',str(a.dispatch_window),
                 '--solver-seed','271828','--low-io','--runtime-profile',a.runtime_profile,'--worker-memory-mib','1536','--queue-policy',a.queue_policy]
            if a.event_driven_settle:cmd.append('--event-driven-settle')
            started=time.perf_counter()
            with active_seed(target), (out/(target.name+'.log')).open('wb')as log:
                done=subprocess.run(cmd,cwd=ROOT,env=base_env,stdout=log,stderr=subprocess.STDOUT)
            wall=time.perf_counter()-started;size=logical_bytes(target)
            resources=read_json(out/(target.name+'-resources.json'))
            row={'seed':seed,'repeat':repeat,'exit_code':done.returncode,'wall_seconds':wall,'written_file_bytes':size,
                 'projected_file_bytes_per_hour':size/wall*3600,
                 'cumulative_write_bytes_per_hour':resources['job_write_transfer_bytes']/max(resources['wall_seconds'],.001)*3600}
            rows.append(row);write_json(out/'runs.json',rows)
            if done.returncode:raise RuntimeError(f'Preflight failed: {target}')
            result=read_json(target/'result.json');semantic=[]
            for record in result['evaluations']:
                path=target/record['label']/'data/decision.json'
                if not path.exists() and not Path(str(path)+'.gz').exists():continue
                decision=read_json(path)
                semantic.append({k:decision.get(k)for k in ['status','phase','reason','trace','observation','decision_evidence','value','native_terminal_observed']})
                k=target.name+'-count_boundary_only'
                checks[k]=checks.get(k,True)and not any(s.get('time_boundary') for s in (decision.get('advisor_metrics')or{}).get('searches',[]))
            blob=canonical(semantic)
            with gzip.open(target/'deterministic-game-result.json.gz','wb')as f:f.write(blob)
            evidence.append(blob)
            checks[target.name+'-semantic_records_present']=bool(semantic)
            checks[target.name+'-all_evaluations_returned']=len(semantic)==len(result['evaluations'])==a.evaluations
            print(json.dumps({'event':'repeat_completed',**row}),flush=True)
        checks['same_trace_and_result_'+seed]=evidence[0]==evidence[1]
    # Real restore from compressed checkpoint vs fresh replay, no advisor.
    source=out/f"seed-{panel['seeds'][0]}-repeat-0"
    paths=list(source.glob('eval-*/data/checkpoints/*.json.gz'))
    checks['compressed_checkpoint_produced']=bool(paths)
    if paths:
        cp=paths[-1];payload=read_json(cp)['payload']
        req={k:payload['context'][k]for k in ['seed','character','ascension','unlocks']}
        req.update(history=payload['history'],generate_candidate=False,low_io=True,event_driven_settle=a.event_driven_settle)
        os.environ['SPIRE_TARGET_CPUS']='8';os.environ['SPIRE_MEMORY_BUDGET_MIB']='14336'
        with NativePool(ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64',out/'restore-workers',ResourcePlan.detect(1,1,1536,1024),runtime_profile=a.runtime_profile)as pool:
            fresh,_=pool.run(req,out/'fresh-reference',120,fresh=True)
            restored,_=pool.run({**req,'checkpoint':str(cp.resolve())},out/'compressed-restore',120,fresh=True)
        checks['compressed_restore_exact']=canonical(fresh['observation'])==canonical(restored['observation']) and canonical(fresh['decision_evidence'])==canonical(restored['decision_evidence'])
    checks['write_rate_below_1GB_hour']=all(r['cumulative_write_bytes_per_hour']<=1_000_000_000 for r in rows)
    report={'passed':all(checks.values()),'checks':checks,'runs':rows,'runtime_profile':a.runtime_profile,'queue_policy':a.queue_policy,'scope':'A10 count preflight; not M0 completion or a 100-combat fidelity audit'}
    write_json(out/'report.json',report);print(json.dumps({'event':'preflight_completed','passed':report['passed'],'checks':checks}),flush=True)
    raise SystemExit(0 if report['passed']else 1)

if __name__=='__main__':main()
