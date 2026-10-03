"""Fresh deterministic seed sets, immutable manifests, resource-limited runs."""
from pathlib import Path
from collections import Counter
from concurrent.futures import ThreadPoolExecutor,as_completed
from threading import Lock
import argparse,datetime,hashlib,json,math,os,random,statistics,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from spire_exact.planning.io import read_json,write_json
from tools.seed_registry import reserve
from tools.experiment_storage import prepare_storage,ensure_capacity
from tools.rolling_storage import sweep,admission,active_seed,RollingConsole

def version_hash(root=None):
    root=ROOT if root is None else Path(root)
    h=hashlib.sha256()
    for directory in ['spire_exact','native']:
        for p in sorted((root/directory).rglob('*')):
            if p.is_file() and p.suffix in ('.py','.cs','.csproj') and not {'bin','obj','__pycache__'}&set(p.parts):
                h.update(p.relative_to(root).as_posix().encode()+b'\0'+p.read_bytes()+b'\0')
    return h.hexdigest()

def wilson(wins,n):
    if not n:return None
    z=1.959963984540054;p=wins/n;den=1+z*z/n
    center=(p+z*z/(2*n))/den;delta=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return [center-delta,center+delta]

def seed_set(run_id,count,excluded):
    r=random.Random(int.from_bytes(hashlib.sha256(run_id.encode()).digest(),'big'));result=[]
    while len(result)<count:
        s=str(r.randrange(1,2**31))
        if s not in excluded and s not in result:result.append(s)
    return result

def summarize(folder,rc,wall):
    path=folder/'result.json';d=read_json(path) if path.exists() else {};ev=d.get('evaluations',[])
    resources=read_json(folder.parent/(folder.name+'-resources.json'))
    certificate=folder/'certificate.json'
    win=d.get('status')=='VERIFIED_WIN_IN_NATIVE_HOST' and certificate.is_file() and not resources['hard_timeout'] and not resources.get('storage_limit') and rc==0
    classes=Counter(x.get('classification','UNKNOWN') for x in ev)
    reasons=[x.get('reason') or '' for x in ev]
    failures=[]
    for p in folder.glob('eval-*/failure.json'):failures.append(read_json(p).get('error',''))
    reasons+=failures
    reason='VERIFIED_WIN' if win else ('STORAGE_LIMIT' if resources.get('storage_limit') else
       'TIMEOUT' if resources['hard_timeout'] or 'time_budget'==d.get('stop_reason') else
       'NATIVE_CRASH' if any('CRASH' in r for r in reasons) else 'UNSUPPORTED_MECHANIC' if classes['MECHANISM_OR_HOST_GAP'] else
       'REPLAY_DIVERGENCE' if classes['RESTORE_OR_REPLAY_MISMATCH'] else 'EXECUTION_ERROR' if rc else
       'COMBAT_FAILURE' if classes['NATIVE_ROUTE_DEATH'] else 'NO_VALID_CANDIDATE' if not ev else 'SEARCH_EXHAUSTED')
    observations=[x.get('observation')or{} for x in ev];far=max(observations,key=lambda x:(x.get('act',-1),x.get('floor',-1)),default={})
    counts=Counter();phase_counts=Counter()
    for e in ev:
        counts.update((e.get('advisor_metrics')or{}).get('counters')or{})
        phase_counts.update((e.get('performance')or{}).get('counters')or{})
    return {'seed':folder.name.removeprefix('seed-'),'win':win,'reason':reason,'exit_code':rc,'wall_seconds':wall,
       'resources':resources,'max_act':far.get('act',-1)+1,'max_floor':far.get('floor',0),
       'evaluations':len(ev),'classifications':dict(classes),'failure_reasons':dict(Counter(r for r in reasons if r)),
       'advisor':dict(counts),'counters':dict(phase_counts),'pool':d.get('pool_stats'),
       'search_metrics':d.get('search_metrics',{}),'result':str(path),'certificate':str(certificate) if win else None}

def main():
    p=argparse.ArgumentParser();p.add_argument('--iteration',required=True);p.add_argument('--run-id',required=True)
    p.add_argument('--kind',choices=['smoke','validation','holdout'],default='validation');p.add_argument('--count',type=int,default=20)
    p.add_argument('--dev-seeds',nargs='*');p.add_argument('--ascension',type=int,default=0)
    p.add_argument('--workers',type=int,choices=[1,2,3,4],default=4)
    p.add_argument('--scheduler',choices=['weighted','dfs','tree','incumbent'],default='weighted')
    p.add_argument('--dispatch',choices=['batch','stream'],default='stream')
    p.add_argument('--repair-mode',choices=['fifo','deep_boss','deep_final_boss'],default='fifo')
    p.add_argument('--boss-repair-ms',type=int,default=30000)
    p.add_argument('--root-portfolio',type=int,choices=[1,2,3,4],default=1)
    p.add_argument('--continuation-mode',choices=['all','complete','none'],default='all')
    p.add_argument('--combat-profile',choices=['Low','Medium','High','VeryHigh','Custom'],default='Low')
    p.add_argument('--combat-ms',type=int,default=400);p.add_argument('--boss-ms',type=int,default=1600)
    p.add_argument('--combat-beam',type=int,help='Optional upstream beam width override')
    p.add_argument('--parallel-seeds',type=int,choices=[1,2,3,4],default=1,
                   help='Development validation only: independent seeds on disjoint8CPU partitions')
    p.add_argument('--full-rollout',action='store_true',help='Use native rollouts to terminal/budget rather than a short floor horizon')
    a=p.parse_args();os.chdir(ROOT)
    cpu_base=int(os.environ.get('SPIRE_CPU_OFFSET','0'))
    if a.kind=='holdout' and a.parallel_seeds!=1:p.error('final holdout runs alone on one fixed CPU partition')
    if cpu_base+8*a.parallel_seeds>os.cpu_count():p.error('not enough logical CPUs for disjoint seed partitions')
    out=ROOT/'experiments'/a.iteration/a.run_id
    if out.exists():raise SystemExit('Run id already exists; never reuse a validation run')
    out.mkdir(parents=True)
    sweep(ROOT,apply=True)
    quota=admission(ROOT)
    storage=prepare_storage(out,a.parallel_seeds)
    storage['rolling_quota']=quota
    # Compile and publish the exact input stamp BEFORE starting timed seed jobs.
    # A plain dotnet build does not create the runner's verified cache stamp.
    from spire_exact.native import build_host
    _,_,build=build_host(ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64')
    write_json(out/'native-build.json',build)
    version=version_hash()
    seeds,ledger=reserve(ROOT,a.run_id,version,a.kind,a.count,a.dev_seeds,seed_set)
    settings=['--character','IRONCLAD','--ascension',str(a.ascension),'--unlocks','all',
      '--workers',str(a.workers),'--dop','1','--worker-memory-mib','1000','--reserve-mib','1536',
      '--evaluations','10000','--seconds','145','--task-seconds','60','--max-decisions','2400',
      '--lookahead-actions','2400' if a.full_rollout else '120','--lookahead-floors','99' if a.full_rollout else '3','--alternatives','4','--survivors','2',
      '--budget-ms',str(a.combat_ms),'--boss-budget-ms',str(a.boss_ms),'--profile',a.combat_profile,
      '--scheduler',a.scheduler,'--dispatch',a.dispatch,'--repair-mode',a.repair_mode,'--boss-repair-ms',str(a.boss_repair_ms),
      '--root-portfolio',str(a.root_portfolio)]
    if a.combat_beam is not None:
        if a.combat_beam<1:raise SystemExit('combat beam must be positive')
        settings+=['--beam',str(a.combat_beam)]
    if a.continuation_mode=='complete':settings+=['--complete-continuations-only']
    if a.continuation_mode=='none':settings+=['--no-continuation-reuse']
    manifest={'version':version,'run_id':a.run_id,'kind':a.kind,'seeds':seeds,'settings':settings,'seed_registry':ledger,
      'timestamp':datetime.datetime.now().astimezone().isoformat(),'fresh_start':True,'protocol':'8C-8GiB-180s-v1',
      'storage':storage,
      'resource_limits':{'cpus':8,'total_memory_gib':8,'os_reserve_gib':2,'workload_job_gib':6,'wall_seconds':180},
      'cpu_partition_offset':int(os.environ.get('SPIRE_CPU_OFFSET','0')),
      'parallel_seed_jobs':a.parallel_seeds,'cpu_partition_offsets':[cpu_base+8*i for i in range(a.parallel_seeds)],
      'game_sha256':hashlib.sha256((ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64/sts2.dll').read_bytes()).hexdigest()}
    write_json(out/'validation-manifest.json',manifest)
    results=[];save_lock=Lock()
    def run_seed(seed,lane):
        ensure_capacity(out,a.parallel_seeds)
        if version_hash()!=version:raise SystemExit('Version changed during validation; results invalidated')
        folder=out/('seed-'+seed)
        cmd=[sys.executable,str(ROOT/'tools/limited_cli.py'),'solve-p5','--seed',seed,'--out',str(folder),
           '--game-dir',str(ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'),*settings]
        start=time.perf_counter();print(json.dumps({'event':'start','seed':seed,'run':a.run_id}),flush=True)
        env=dict(os.environ,SPIRE_CPU_OFFSET=str(cpu_base+8*lane))
        with active_seed(folder):
            log=RollingConsole(out/('seed-'+seed+'.log'))
            try:
                with subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,cwd=ROOT,env=env) as process:
                    while chunk:=process.stdout.read(65536):log.write(chunk)
                    rc=process.wait()
            finally:log.close()
        row=summarize(folder,rc,time.perf_counter()-start)
        row['cpu_partition_offset']=cpu_base+8*lane
        if version_hash()!=version:raise SystemExit('Version changed during validation; results invalidated')
        return row
    def save_row(row):
        # A single writer aggregates evidence; output follows manifest seed order.
        seed=row['seed']
        results.append(row);results.sort(key=lambda r:seeds.index(r['seed']))
        write_json(out/'results.json',results)
        wins=sum(r['win'] for r in results);times=[r['wall_seconds'] for r in results]
        stats={'wins':wins,'tested':len(results),'planned':len(seeds),'rate':wins/len(results),
          'wilson95':wilson(wins,len(results)),'complete':len(results)==len(seeds),
          'reasons':dict(Counter(r['reason'] for r in results)),
          'mean_seconds':statistics.mean(times),'median_seconds':statistics.median(times),
          'p95_seconds':sorted(times)[max(0,math.ceil(.95*len(times))-1)],
          'act2_reach':sum(r['max_act']>=2 for r in results),'act3_reach':sum(r['max_act']>=3 for r in results),
          'version':version,'normal_godot_verified':False}
        stats['valid_for_generalization_rate']=not any(r['reason']=='STORAGE_LIMIT' for r in results)
        if not stats['valid_for_generalization_rate']:stats['environment_failure']='STORAGE_LIMIT'
        write_json(out/'performance.json',stats)
        write_json(out/'failure-summary.json',[r for r in results if not r['win']])
        print(json.dumps({'event':'result','seed':seed,'win':row['win'],'reason':row['reason'],'floor':row['max_floor'],'wins':wins,'tested':len(results)}),flush=True)
    def run_lane(lane):
        for seed in seeds[lane::a.parallel_seeds]:
            row=run_seed(seed,lane)
            with save_lock:
                save_row(row)
                sweep(ROOT,apply=True)
                admission(ROOT)
    if a.parallel_seeds==1:run_lane(0)
    else:
        with ThreadPoolExecutor(max_workers=a.parallel_seeds) as executor:
            for future in as_completed([executor.submit(run_lane,lane) for lane in range(a.parallel_seeds)]):future.result()

if __name__=='__main__':main()
