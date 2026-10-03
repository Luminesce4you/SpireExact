"""A user-requested fresh seed, queued on held process handles, bounded by a Job."""
from pathlib import Path
import argparse,ctypes as c,datetime,json,os,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from dashboard.jobs import process_identity
from spire_exact.planning.io import read_json,write_json

def wait_original(item):
    k=c.WinDLL('kernel32',use_last_error=True);k.OpenProcess.argtypes=[c.c_ulong,c.c_int,c.c_ulong];k.OpenProcess.restype=c.c_void_p
    k.WaitForSingleObject.argtypes=[c.c_void_p,c.c_ulong];k.CloseHandle.argtypes=[c.c_void_p]
    handle=k.OpenProcess(0x100000,False,item['pid'])
    if not handle:return
    try:
        if process_identity(item['pid'])==item and k.WaitForSingleObject(handle,0xffffffff)!=0:raise c.WinError(c.get_last_error())
    finally:k.CloseHandle(handle)

def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);a=p.parse_args();folder=a.run.resolve()
    request=read_json(folder/'manual-request.json');workspace=Path(request['workspace'])
    def event(kind,**values):
        with(folder/'events.jsonl').open('a',encoding='utf-8')as f:f.write(json.dumps({'event':kind,'wall_seconds':time.perf_counter()-started,**values})+'\n')
    started=time.perf_counter()
    try:
        for item in request['wait_for']:wait_original(item)
        # Dashboard is on E cores; explicitly select the validated P partition
        # before spawning the solver instead of inheriting the server's mask.
        from tools.cpu_topology import inventory,homogeneous_cpus
        cpus,cls=homogeneous_cpus(inventory(),8)
        k=c.WinDLL('kernel32',use_last_error=True);k.GetCurrentProcess.restype=c.c_void_p;k.SetProcessAffinityMask.argtypes=[c.c_void_p,c.c_size_t]
        if not k.SetProcessAffinityMask(k.GetCurrentProcess(),sum(1<<i for i in cpus)):raise c.WinError(c.get_last_error())
        from tools.experiment_storage import prepare_storage
        from tools.rolling_storage import admission,active_seed,RollingConsole,logical_bytes
        admission(ROOT);prepare_storage(folder)
        manifest=read_json(folder/'validation-manifest.json');manifest.update(timestamp=datetime.datetime.now().astimezone().isoformat(),cpu_set=cpus,efficiency_class=cls)
        write_json(folder/'validation-manifest.json',manifest);write_json(folder/'launch-state.json',{'phase':'running'})
        started=time.perf_counter();seconds=request['minutes']*60
        target=folder/('seed-'+request['seed'])
        data=workspace/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
        settings=['--seed',request['seed'],'--character','IRONCLAD','--ascension','10','--unlocks','all','--workers','7','--dop','1',
                  '--worker-memory-mib','1536','--reserve-mib','1024','--evaluations','1000000','--seconds',str(max(10,seconds-30)),
                  '--task-seconds',str(min(1200,seconds)),'--max-decisions','12000','--lookahead-actions','12000','--lookahead-floors','99',
                  '--alternatives','8','--survivors','2','--budget-ms','600000','--boss-budget-ms','600000','--nodes','60000','--profile','Low',
                  '--dispatch','ordered','--dispatch-window','56','--solver-seed','271828','--low-io','--event-driven-settle',
                  '--checkpoint-mib','1024','--cache-mib','128','--archive-entries','256','--runtime-profile',request['runtime_profile']]
        manifest['settings']=settings;write_json(folder/'validation-manifest.json',manifest)
        cmd=[sys.executable,str(workspace/'tools/limited_cli.py'),'solve-p5','--out',str(target),'--game-dir',str(data),*settings]
        env=dict(os.environ,SPIRE_PROTOCOL='A10-seed-v2',SPIRE_WALL_LIMIT_SECONDS=str(seconds),PYTHONIOENCODING='utf-8')
        log=RollingConsole(folder/'console.log');furthest=-1
        try:
            with active_seed(target),subprocess.Popen(cmd,cwd=workspace,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                                                     creationflags=subprocess.CREATE_NO_WINDOW)as proc:
                write_json(folder/'process.json',{'coordinator_pid':os.getpid(),'coordinator_identity':process_identity(os.getpid()),'owned_job_pid':proc.pid})
                event('baseline_started',seed=request['seed'],protocol='A10-seed-v2',cpu_set=cpus)
                for line in iter(proc.stdout.readline,b''):
                    log.write(line)
                    try:row=json.loads(line)
                    except (ValueError,UnicodeDecodeError):continue
                    floor=row.get('floor')
                    if isinstance(floor,int)and floor>furthest:
                        furthest=floor;event('new_furthest_evaluation',floor=floor,label=row.get('label'),classification=row.get('classification'))
                code=proc.wait()
        finally:log.close()
        result=read_json(target/'result.json')if(target/'result.json').exists()else{}
        resource_path=folder/(target.name+'-resources.json');resource=read_json(resource_path)if resource_path.exists()else{}
        win=code==0 and result.get('status')=='VERIFIED_WIN_IN_NATIVE_HOST'and(target/'certificate.json').exists()
        report={'protocol':'A10-seed-v2','seed':request['seed'],'verified_win':win,'censored':not win,'exit_code':code,
                'wall_seconds':time.perf_counter()-started,'resources':resource,'file_bytes':logical_bytes(folder),
                'normal_godot_verified':False,'not_a_seed_infeasibility_proof':True,'user_interactive':True}
        write_json(folder/'baseline-report.json',report);write_json(folder/'launch-state.json',{'phase':'completed'if code in(0,124)else'error'})
        event('baseline_completed'if code in(0,124)else'baseline_failed',verified_win=win,exit_code=code)
    except Exception as error:
        write_json(folder/'launch-state.json',{'phase':'error','message':str(error)})
        event('baseline_failed',exit_code=1,error=str(error));raise

if __name__=='__main__':main()
