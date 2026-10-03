"""Start one three-hour A10 DEV baseline; react to process events, not polling."""
from pathlib import Path
import argparse,datetime,json,os,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.rolling_storage import owner_root,admission,active_seed,RollingConsole,logical_bytes
from tools.experiment_storage import prepare_storage
from tools.experiment import version_hash
from tools.cpu_topology import inventory,homogeneous_cpus
from spire_exact.native import build_host
from spire_exact.planning.io import read_json,write_json

def main():
    p=argparse.ArgumentParser();p.add_argument('--run-id',required=True);p.add_argument('--dev-index',type=int,default=0)
    p.add_argument('--solver-seed',type=int,default=271828)
    p.add_argument('--dispatch',choices=['batch','ordered'],default='ordered')
    p.add_argument('--dispatch-window',type=int,default=56)
    p.add_argument('--iteration',default='iteration-022')
    p.add_argument('--event-driven-settle',action='store_true')
    p.add_argument('--preflight',type=Path);a=p.parse_args()
    owner=owner_root(ROOT)
    gate_path=a.preflight or owner/'experiments/iteration-021/m0-preflight/report.json'
    gate=read_json(gate_path)
    if not gate.get('passed'):raise SystemExit('A10 launch gate failed; no long run started')
    panel=read_json(owner/'experiments/panels/A10-seed-v2/dev.json');seed=panel['seeds'][a.dev_index]
    out=ROOT/'experiments'/a.iteration/a.run_id;out.mkdir(parents=True,exist_ok=False)
    prepare_storage(out);admission(ROOT)
    data=ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
    _,_,build=build_host(data)
    cpus,cls=homogeneous_cpus(inventory(),8)
    settings=['--seed',seed,'--character','IRONCLAD','--ascension','10','--unlocks','all',
              '--workers','7','--dop','1','--worker-memory-mib','640','--reserve-mib','1024',
              '--evaluations','1000000','--seconds','10740','--task-seconds','3600','--max-decisions','12000',
              '--lookahead-actions','12000','--lookahead-floors','99','--alternatives','8','--survivors','2',
              '--budget-ms','600000','--boss-budget-ms','600000','--nodes','60000','--profile','Low',
              '--dispatch',a.dispatch,'--dispatch-window',str(a.dispatch_window),'--solver-seed',str(a.solver_seed),'--low-io',
              '--checkpoint-mib','1024','--cache-mib','128','--archive-entries','256']
    if a.event_driven_settle:settings.append('--event-driven-settle')
    manifest={'protocol':'A10-seed-v2','stage':'M0 baseline; fidelity/utilization exit gates still under measurement',
              'version':version_hash(),'timestamp':datetime.datetime.now().astimezone().isoformat(),'run_id':a.run_id,
              'panel':'DEV','seed':seed,'solver_seed':a.solver_seed,'settings':settings,'cpu_set':cpus,'efficiency_class':cls,
              'report_point_seconds':9000,'wall_cap_seconds':10800,'workload_bytes':14*1024**3,'os_reserve_bytes':2*1024**3,
              'memory_protocol_override':'User explicitly authorized 16 GiB total on 2026-09-30; CPU and wall budgets unchanged',
              'native_build':build,'preflight':str(gate_path),
              'old_prefixes_loaded':False,'normal_godot_verified':False,
              'count_budgets':{'combat_nodes_per_search':60000,'evaluations':1000000,'native_actions_per_evaluation':12000},
              'safety_caps':{'combat_wall_ms':600000,'task_wall_seconds':3600},
              'other_solver_jobs_at_launch':'none expected; topology and commands recorded separately'}
    write_json(out/'validation-manifest.json',manifest)
    target=out/('seed-'+seed);events=out/'events.jsonl';started=time.perf_counter()
    def hook(kind,**values):
        event={'event':kind,'wall_seconds':time.perf_counter()-started,**values}
        with events.open('a',encoding='utf-8')as f:f.write(json.dumps(event)+'\n')
        print(json.dumps(event),flush=True)
    cmd=[sys.executable,str(ROOT/'tools/limited_cli.py'),'solve-p5','--out',str(target),'--game-dir',str(data),*settings]
    env=dict(os.environ,SPIRE_PROTOCOL='A10-seed-v2',SPIRE_WALL_LIMIT_SECONDS='10800',PYTHONIOENCODING='utf-8')
    hook('baseline_started',seed=seed,protocol=manifest['protocol'],cpu_set=cpus)
    log=RollingConsole(out/'console.log');best_floor=-1
    try:
        with active_seed(target),subprocess.Popen(cmd,cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)as process:
            write_json(out/'process.json',{'coordinator_pid':os.getpid(),'owned_job_pid':process.pid,'command':cmd})
            for line in iter(process.stdout.readline,b''):
                log.write(line)
                try:row=json.loads(line)
                except (ValueError,UnicodeDecodeError):continue
                floor=row.get('floor')
                if isinstance(floor,int)and floor>best_floor:
                    best_floor=floor;hook('new_furthest_evaluation',floor=floor,label=row.get('label'),classification=row.get('classification'))
                if row.get('classification')=='NATIVE_WIN_CANDIDATE':hook('native_win_candidate',label=row.get('label'))
            code=process.wait()
    finally:log.close()
    result=read_json(target/'result.json')if(target/'result.json').exists()else{}
    resource=read_json(out/(target.name+'-resources.json'))
    win=code==0 and result.get('status')=='VERIFIED_WIN_IN_NATIVE_HOST' and(target/'certificate.json').exists()and not resource.get('storage_limit')
    wall=time.perf_counter()-started;size=logical_bytes(out)
    report={'protocol':manifest['protocol'],'seed':seed,'solver_seed':a.solver_seed,'verified_win':win,
            'censored':not win,'exit_code':code,'resources':resource,'wall_seconds':wall,'file_bytes':size,
            'file_bytes_per_hour':size/max(wall,1)*3600,'normal_godot_verified':False,'result':str(target/'result.json'),
            'not_a_seed_infeasibility_proof':True}
    write_json(out/'baseline-report.json',report)
    hook('baseline_completed'if code==0 else'baseline_failed',verified_win=win,exit_code=code,report=str(out/'baseline-report.json'))
    raise SystemExit(code)

if __name__=='__main__':main()
