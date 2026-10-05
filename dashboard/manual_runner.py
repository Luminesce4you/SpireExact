"""A user-requested fresh seed, queued on held process handles, bounded by a Job."""
from pathlib import Path
import argparse,ctypes as c,datetime,json,os,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from dashboard.jobs import process_identity
from spire_exact.planning.io import read_json,write_json
from dashboard import job_control

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
        elapsed=job_control.status(folder)['active_elapsed_seconds'] if (folder/'control.json').exists() else time.perf_counter()-started
        with(folder/'events.jsonl').open('a',encoding='utf-8')as f:f.write(json.dumps({'event':kind,'wall_seconds':elapsed,**values})+'\n')
    started=time.perf_counter()
    try:
        # WMI-detached starts do not inherit the HTTP process's temporary PATH.
        # Reconstruct the project SDK environment in the actual runner.
        setup=read_json(ROOT/'.tools/source-setup.json')
        sdk=Path(setup['dotnet']).resolve().parent
        if not Path(setup['dotnet']).is_file():raise RuntimeError('Configured .NET SDK is unavailable; run source setup again')
        os.environ['PATH']=str(sdk)+os.pathsep+os.environ.get('PATH','')
        os.environ['DOTNET_ROOT']=str(sdk)
        os.environ['DOTNET_CLI_TELEMETRY_OPTOUT']='1'
        job_control.initialize(folder)
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
        ledger=folder/'pause-ledger.json'
        from tools.prepare_dashboard import validate_ready
        prepared=validate_ready(read_json(ROOT/'dashboard/solver-profile.json'))
        if (workspace.resolve()!=ROOT.resolve() or manifest.get('version')!=prepared['source_version']
                or manifest.get('host_sha256')!=prepared['host_sha256']):
            raise RuntimeError('Queued frontend source identity changed; prepare and submit a new run')
        pause_supported=prepared['capabilities'].get('interactive_pause_clock')=='evaluate-native-and-python/v1'
        manifest.update(process_control_schema=job_control.SCHEMA,
                        pause_excluded_budget_clock=pause_supported,pause_ledger=str(ledger.resolve()),
                        pause_scope='live_named_job_memory_only',durable_restart_supported=False)
        write_json(folder/'validation-manifest.json',manifest)
        job_control.mark_running(folder)
        write_json(folder/'launch-state.json',{'phase':'running'})
        started=time.perf_counter();seconds=request['minutes']*60
        target=folder/('seed-'+request['seed'])
        data=workspace/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
        from dashboard.jobs import solver_settings
        settings=solver_settings(request,read_json(ROOT/'dashboard/solver-profile.json'))
        if request.get('settings')!=settings:
            raise RuntimeError('Queued frontend arguments differ from the validated public recipe')
        from tools.run_release_source import effective_parameters
        manifest['effective_parameters']=effective_parameters(['--out',str(target),'--game-dir',str(data),*settings])
        manifest['settings']=settings;write_json(folder/'validation-manifest.json',manifest)
        cmd=[sys.executable,str(workspace/'tools/limited_cli.py'),'solve-p5','--out',str(target),'--game-dir',str(data),*settings]
        env=dict(os.environ,SPIRE_PROTOCOL='A10-seed-v2',SPIRE_WALL_LIMIT_SECONDS=str(seconds),PYTHONIOENCODING='utf-8',
                 SPIRE_PAUSE_LEDGER=str(ledger.resolve()),SPIRE_REQUIRE_EXACT_WORKERS='1')
        for key in ('SPIRE_JOB_MEMORY_MIB','SPIRE_RESOURCE_OVERRIDE_NOTE','SPIRE_CPU_OFFSET'):env.pop(key,None)
        if not pause_supported:env.pop('SPIRE_PAUSE_LEDGER',None)
        log=RollingConsole(folder/'console.log');furthest=-1
        try:
            with active_seed(target),subprocess.Popen(cmd,cwd=workspace,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                                                     creationflags=subprocess.CREATE_NO_WINDOW)as proc:
                write_json(folder/'process.json',{'coordinator_pid':os.getpid(),'coordinator_identity':process_identity(os.getpid()),
                                                 'owned_job_pid':proc.pid,'owned_job_identity':process_identity(proc.pid)})
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
        active_seconds=job_control.status(folder)['active_elapsed_seconds']
        report={'protocol':'A10-seed-v2','seed':request['seed'],'verified_win':win,'censored':not win,'exit_code':code,
                'wall_seconds':active_seconds,'raw_wall_seconds':time.perf_counter()-started,
                'pause_excluded_budget_clock':pause_supported,'resources':resource,'file_bytes':logical_bytes(folder),
                'normal_godot_verified':False,'not_a_seed_infeasibility_proof':True,'user_interactive':True}
        write_json(folder/'baseline-report.json',report)
        phase='completed'if code in(0,124)else'error'
        if job_control.mark_finished(folder,phase):write_json(folder/'launch-state.json',{'phase':phase})
        event('baseline_completed'if code in(0,124)else'baseline_failed',verified_win=win,exit_code=code)
    except Exception as error:
        if job_control.mark_finished(folder,'error'):
            write_json(folder/'launch-state.json',{'phase':'error','message':str(error)})
        event('baseline_failed',exit_code=1,error=str(error));raise

if __name__=='__main__':main()
