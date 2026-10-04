"""Short real Windows queued-Job control check; no game or solver is launched."""
from pathlib import Path
import argparse,json,os,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.planning.io import read_json,write_json
from tools.interactive_seed_control import attach,process_record
from dashboard import jobs
from spire_exact.pausable_clock import active_counter

def worker(folder,role):
    if role=='coordinator':
        workspace=(ROOT/'dashboard/solver-profile.json')
        profile=read_json(workspace);workspace=(ROOT/profile['workspace']).resolve()
        freeze=read_json(workspace/'freeze.json')
        manifest={'run_id':'queued-control-runtime-check','seed':'0','workspace':str(workspace),
                  'host_sha256':freeze['capabilities']['validated_host_sha256'],'version':freeze['source_version'],
                  'settings':['--gate-preset','escalate-evaluate','--final-gate-plan','open'],
                  'counts_as_planner_win':False,'scope':'counter processes only; no game or seed search'}
        control,on=attach(folder,manifest)
        if not on:raise AssertionError('Current dashboard report must enable the queued pause clock')
        os.environ['SPIRE_PAUSE_LEDGER']=manifest['pause_ledger']
        write_json(folder/'validation-manifest.json',manifest)
        control.mark_running(folder);write_json(folder/'launch-state.json',{'phase':'running'})
        child=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--out',str(folder),'--worker','leaf'],
                               cwd=ROOT,creationflags=subprocess.CREATE_NO_WINDOW)
        process_record(folder,child.pid)
    count=0
    while True:
        write_json(folder/(role+'.json'),{'pid':os.getpid(),'count':count,'active_seconds':active_counter()})
        count+=1;time.sleep(.02)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True);parser.add_argument('--worker',choices=['coordinator','leaf'])
    args=parser.parse_args();folder=args.out.resolve()
    if args.worker:worker(folder,args.worker);return
    if folder.exists():raise ValueError('Use a new check directory')
    folder.mkdir(parents=True)
    with (folder/'process.log').open('wb') as log:
        proc=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--out',str(folder),'--worker','coordinator'],
                              cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
    report={'scope':'nonmanual API and actual counter Job pause/resume/stop; no game, solver or full run',
            'native_actions':0,'solver_runs':0,'passed':False}
    class Repository:paths={'queued-control-runtime-check':folder/'validation-manifest.json'}
    repo=Repository()
    def rows():return {name:read_json(folder/(name+'.json')) for name in ('coordinator','leaf')}
    def wait(condition):
        deadline=time.perf_counter()+10
        while time.perf_counter()<deadline:
            try:value=rows()
            except (OSError,ValueError):value={}
            if value and condition(value):return value
            if proc.poll() is not None:raise RuntimeError('Counter coordinator exited; see process.log')
            time.sleep(.02)
        raise RuntimeError('Counter check timed out')
    try:
        original=wait(lambda value:all(row['count']>=3 for row in value.values()))
        manifest=read_json(folder/'validation-manifest.json');assert not manifest.get('manual')
        paused=jobs.control('queued-control-runtime-check','pause',{},repo)['job_control']
        before=rows();time.sleep(.35);after=rows()
        assert before==after
        assert abs(jobs.control_status(folder)['elapsed_seconds']-paused['elapsed_seconds'])<.05
        report.update(nonmanual_api_pause=True,all_counters_frozen=True,active_budget_frozen=True)
        jobs.control('queued-control-runtime-check','resume',{},repo)
        progressed=wait(lambda value:all(value[name]['count']>after[name]['count'] for name in value))
        # The inherited reader subtracts the physical paused interval for the child too.
        assert all(0<progressed[name]['active_seconds']-after[name]['active_seconds']<.20 for name in progressed)
        report.update(nonmanual_api_resume=True,inherited_pause_clock=True)
        jobs.control('queued-control-runtime-check','pause',{},repo)
        stopped=jobs.control('queued-control-runtime-check','stop',{},repo)['job_control']
        proc.wait(timeout=5)
        assert stopped['phase']=='stopped' and not stopped['can_stop'] and not stopped['can_resume']
        deadline=time.perf_counter()+5
        while any(jobs.process_identity(row['pid']) for row in progressed.values()) and time.perf_counter()<deadline:time.sleep(.02)
        assert not any(jobs.process_identity(row['pid']) for row in progressed.values())
        report.update(nonmanual_api_paused_stop=True,owned_processes_exited=True,passed=True)
    finally:
        if proc.poll() is None:
            try:jobs.control('queued-control-runtime-check','stop',{},repo)
            except Exception:pass
        write_json(folder/'report.json',report)
    print(json.dumps(report));assert report['passed']

if __name__=='__main__':main()
