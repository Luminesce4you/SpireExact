"""Same native work under P-core 8T / 16T masks; optional completion hook.

All workers share a Job affinity mask. There is no per-worker core binding.
Stored prefixes are diagnostic work only, never fresh planner wins.
"""
from pathlib import Path
import argparse,ctypes as c,json,os,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.planning.io import read_json,write_json
from spire_exact.planning.pool import NativePool
from spire_exact.planning.resources import ResourcePlan
from spire_exact.native import build_host
from spire_exact.canonical import canonical
from tools.rolling_storage import owner_root,active_seed
from tools.cpu_topology import inventory,homogeneous_cpus

OUT=ROOT/'experiments/iteration-024/smt-runtime-pairs'
ARMS={'8t-legacy':(8,7,'legacy'),'16t-workers7':(16,7,'legacy'),
      '16t-workers15':(16,15,'legacy'),'16t-runtime2':(16,15,'workstation-2')}
SOURCE=owner_root(ROOT)/'experiments/frozen-i024/experiments/iteration-023/full-route-io-preflight/seed-564940356-repeat-0'

def event(kind,**fields):
    row={'event':kind,'time':time.time(),**fields}
    with(OUT/'events.jsonl').open('a',encoding='utf-8')as f:f.write(json.dumps(row)+'\n')
    print(json.dumps(row),flush=True)

def component(arm,out):
    _,workers,runtime=ARMS[arm]
    records=read_json(SOURCE/'result.json')['evaluations']
    specs=[]
    for record in records:
        req=read_json(SOURCE/record['label']/'request.json')
        req.pop('checkpoint',None)
        req.update(capture_checkpoints=False,low_io=True,event_driven_settle=True)
        specs.append(req)
    if len(specs)!=28:raise RuntimeError('The paired source must contain exactly 28 complete requests')
    # Fixed duplicate block gives 56 tasks to every arm; report it explicitly.
    specs=specs+specs
    rows=[];data=ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
    plan=ResourcePlan.detect(workers,1,800,1024)
    if plan.workers!=workers:raise RuntimeError('Requested worker count cannot fit; do not silently compare fewer workers')
    with NativePool(data,out/'workers',plan,runtime_profile=runtime)as pool:
        futures=[pool.submit(req,out/f'case-{i:03}',1200)for i,req in enumerate(specs)]
        for i,future in enumerate(futures):
            result,_=future.result();perf=result['performance']
            if result['status']not in('TERMINAL','BUDGET','DECISION')or len(result.get('trace',[]))<len(specs[i]['history']):
                raise RuntimeError('Invalid native component outcome; no throughput promotion')
            nodes=sum(s.get('expanded_nodes')or 0 for s in(result.get('advisor_metrics')or{}).get('searches',[]))
            expected=1 if runtime=='legacy'else 2
            if perf['runtime_processor_count']!=expected or perf['gc_server']:raise RuntimeError('Observed CLR profile differs from requested profile')
            rows.append({'index':i,'nodes':nodes,'status':result['status'],'native_wall_us':perf['wall_us'],'native_cpu_us':perf['cpu_us']})
    write_json(out/'component.json',{'arm':arm,'rows':rows,'requests':56,'unique_source_requests':28,
        'source_block_repeated':2,'resources':plan.as_dict(),'runtime_profile':runtime,'counts_as_planner_win':False})

def main():
    p=argparse.ArgumentParser();p.add_argument('--child',choices=ARMS);p.add_argument('--repeat',type=int,default=0)
    p.add_argument('--wait-for-pid',type=int);a=p.parse_args()
    if a.child:
        from tools import limited_cli
        out=OUT/f'repeat-{a.repeat}-{a.child}'
        sys.argv=[__file__,'--out',str(out)]
        limited_cli.runpy.run_module=lambda *args,**kwargs:component(a.child,out)
        limited_cli.main();return
    OUT.mkdir(parents=True,exist_ok=False)
    topology=inventory();p16,cls=homogeneous_cpus(topology,16)
    if cls!=max(r['efficiency_class']for r in topology):raise RuntimeError('16T P-core partition unavailable')
    k=c.WinDLL('kernel32',use_last_error=True)
    k.OpenProcess.argtypes=[c.c_ulong,c.c_int,c.c_ulong];k.OpenProcess.restype=c.c_void_p
    k.WaitForSingleObject.argtypes=[c.c_void_p,c.c_ulong];k.CloseHandle.argtypes=[c.c_void_p]
    if a.wait_for_pid:
        handle=k.OpenProcess(0x100000,False,a.wait_for_pid)
        if not handle:raise c.WinError(c.get_last_error())
        event('waiting_for_baseline_process_exit',pid=a.wait_for_pid)
        try:
            if k.WaitForSingleObject(handle,0xffffffff)!=0:raise c.WinError(c.get_last_error())
        finally:k.CloseHandle(handle)
    k.GetCurrentProcess.restype=c.c_void_p;k.SetProcessAffinityMask.argtypes=[c.c_void_p,c.c_size_t]
    if not k.SetProcessAffinityMask(k.GetCurrentProcess(),sum(1<<i for i in p16)):raise c.WinError(c.get_last_error())
    data=ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
    _,_,build=build_host(data)
    write_json(OUT/'validation-manifest.json',{'run_id':'smt-runtime-components','protocol':'separate A10 8T and 16T component profiles',
        'arms':ARMS,'cpu_topology':topology,'native_build':build,'counts_as_planner_win':False,'source':str(SOURCE),
        'workload_bytes':14*1024**3,'os_reserve_bytes':2*1024**3,'source_requests':28,'repeated_blocks':2,
        'waited_for_pid':a.wait_for_pid,'per_arm_wall_cap_seconds':1800,'solver_dop':1,
        'scope':'counterbalanced same-work component; no full-game scaling or fresh-win-rate claim'})
    complete=[]
    for repeat in range(2):
        for arm in(list(ARMS)if repeat==0 else list(ARMS)[::-1]):
            protocol='A10-seed-v2'if ARMS[arm][0]==8 else'A10-seed-v2-scaling16'
            env=dict(os.environ,SPIRE_PROTOCOL=protocol,SPIRE_WALL_LIMIT_SECONDS='1800',PYTHONIOENCODING='utf-8')
            target=OUT/f'repeat-{repeat}-{arm}'
            event('component_started',arm=arm,repeat=repeat,protocol=protocol)
            with active_seed(target),(OUT/f'repeat-{repeat}-{arm}.log').open('wb')as log:
                done=subprocess.run([sys.executable,__file__,'--child',arm,'--repeat',str(repeat)],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
            if done.returncode:
                event('component_failed',arm=arm,repeat=repeat,exit_code=done.returncode);raise SystemExit(done.returncode)
            resource=read_json(OUT/(target.name+'-resources.json'))
            complete.append({'arm':arm,'repeat':repeat,'resources':resource,'component':read_json(target/'component.json')})
            write_json(OUT/'partial-results.json',complete)
            event('component_completed',arm=arm,repeat=repeat,wall_seconds=resource['wall_seconds'],cpu_seconds=resource['job_cpu_seconds'])
    reference=OUT/'repeat-0-8t-legacy';comparisons=[]
    keys=['status','phase','reason','value','observation','trace','decision_evidence','native_terminal_observed']
    ref_nodes=read_json(reference/'component.json')['rows']
    for run in complete:
        path=OUT/f"repeat-{run['repeat']}-{run['arm']}";same=True
        for i,row in enumerate(run['component']['rows']):
            left=read_json(reference/f'case-{i:03}/data/decision.json');right=read_json(path/f'case-{i:03}/data/decision.json')
            same &=canonical({key:left.get(key)for key in keys})==canonical({key:right.get(key)for key in keys})and row['nodes']==ref_nodes[i]['nodes']
        comparisons.append({'arm':run['arm'],'repeat':run['repeat'],'full_game_and_nodes_equal':same})
    report={'passed':all(r['full_game_and_nodes_equal']for r in comparisons),'complete':True,'comparisons':comparisons,'runs':complete,'counts_as_planner_win':False}
    write_json(OUT/'report.json',report);event('smt_benchmark_completed',passed=report['passed'])
    raise SystemExit(0 if report['passed']else 1)

if __name__=='__main__':main()
