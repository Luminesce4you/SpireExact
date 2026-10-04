"""Run the solver inside one Windows Job Object (8 CPUs, bounded memory).

A10 uses the user-approved 16 GiB model (14 GiB workload + 2 GiB reserve).
Legacy A0 retains its original 6 GiB workload + 2 GiB reserve.
The job contains the coordinator and every child; closing it kills owned workers.
"""
import ctypes as c
from ctypes import wintypes as w
import json, os, runpy, sys, threading, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));os.chdir(ROOT)
from tools.rolling_storage import acquire_job_slot, admission, JOB_WRITE_CAP
from tools.cpu_topology import inventory,homogeneous_cpus
from spire_exact.pausable_clock import active_counter,paused_seconds

class Basic(c.Structure):
    _fields_=[('PerProcessUserTimeLimit',c.c_longlong),('PerJobUserTimeLimit',c.c_longlong),
      ('LimitFlags',w.DWORD),('MinimumWorkingSetSize',c.c_size_t),('MaximumWorkingSetSize',c.c_size_t),
      ('ActiveProcessLimit',w.DWORD),('Affinity',c.c_size_t),('PriorityClass',w.DWORD),('SchedulingClass',w.DWORD)]
class IO(c.Structure):
    _fields_=[(x,c.c_ulonglong) for x in ['ReadOperationCount','WriteOperationCount','OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount']]
class Extended(c.Structure):
    _fields_=[('BasicLimitInformation',Basic),('IoInfo',IO),('ProcessMemoryLimit',c.c_size_t),
      ('JobMemoryLimit',c.c_size_t),('PeakProcessMemoryUsed',c.c_size_t),('PeakJobMemoryUsed',c.c_size_t)]
class Accounting(c.Structure):
    _fields_=[(x,c.c_longlong) for x in ['TotalUserTime','TotalKernelTime','ThisPeriodTotalUserTime','ThisPeriodTotalKernelTime']]+[(x,w.DWORD) for x in ['TotalPageFaultCount','TotalProcesses','ActiveProcesses','TotalTerminatedProcesses']]
class AccountingIO(c.Structure):
    _fields_=[('BasicInfo',Accounting),('IoInfo',IO)]

def job_memory_mib(protocol, env):
    a10=protocol in ('A10-seed-v2','A10-seed-v2-scaling16')
    default=14336 if a10 else 6144
    if 'SPIRE_JOB_MEMORY_MIB' not in env:return default
    value=int(env['SPIRE_JOB_MEMORY_MIB'])
    if not a10 or not env.get('SPIRE_RESOURCE_OVERRIDE_NOTE','').strip():
        raise ValueError('Job memory override requires A10 and a recorded authorization note')
    if not 1024<=value<=28672:raise ValueError('Job memory override outside 1024..28672 MiB')
    return value

def main():
    if os.name!='nt':raise SystemExit('This runner requires Windows Job Objects; no unenforced fallback')
    lease=acquire_job_slot(ROOT)  # Keep open until process exit, including hard cancellation.
    admission(ROOT)
    args=sys.argv[1:];out=Path(args[args.index('--out')+1]).resolve()
    out.mkdir(parents=True,exist_ok=True)
    # The CLI insists on an empty output directory, so telemetry stays beside it.
    telemetry=out.parent/(out.name+'-resources.json')
    active_marker=out.parent/(out.name+'.job-active.json')
    active_marker.write_text(json.dumps({'pid':os.getpid(),'time':time.time()}))
    k=c.WinDLL('kernel32',use_last_error=True)
    k.CreateJobObjectW.argtypes=[c.c_void_p,w.LPCWSTR];k.CreateJobObjectW.restype=w.HANDLE
    k.GetCurrentProcess.restype=w.HANDLE
    k.SetInformationJobObject.argtypes=[w.HANDLE,c.c_int,c.c_void_p,w.DWORD]
    k.QueryInformationJobObject.argtypes=[w.HANDLE,c.c_int,c.c_void_p,w.DWORD,c.c_void_p]
    k.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE]
    k.TerminateJobObject.argtypes=[w.HANDLE,w.UINT]
    k.GetProcessAffinityMask.argtypes=[w.HANDLE,c.POINTER(c.c_size_t),c.POINTER(c.c_size_t)]
    k.SetProcessAffinityMask.argtypes=[w.HANDLE,c.c_size_t]
    process=k.GetCurrentProcess();allowed=c.c_size_t();system=c.c_size_t()
    if not k.GetProcessAffinityMask(process,c.byref(allowed),c.byref(system)):raise c.WinError(c.get_last_error())
    protocol=os.environ.get('SPIRE_PROTOCOL','A0-180s-v1')
    a10=protocol in('A10-seed-v2','A10-seed-v2-scaling16')
    logical_cpus=16 if protocol=='A10-seed-v2-scaling16'else 8
    wall_limit=int(os.environ.get('SPIRE_WALL_LIMIT_SECONDS','10800' if a10 else '180'))
    if wall_limit<1 or wall_limit>(10800 if a10 else 180):raise SystemExit('Wall safety cap outside selected protocol')
    offset=int(os.environ.get('SPIRE_CPU_OFFSET','0'))
    if offset<0:raise SystemExit('CPU offset must be nonnegative')
    efficiency_class=None
    if a10:
        rows=[r for r in inventory() if allowed.value&(1<<r['logical_cpu'])]
        cores,efficiency_class=homogeneous_cpus(rows,logical_cpus)
    else:cores=[i for i in range(64) if allowed.value&(1<<i)][offset:offset+8]
    if len(cores)!=logical_cpus:raise SystemExit('Requested logical-CPU partition unavailable')
    physical_count=len({r['core_index']for r in inventory()if r['logical_cpu']in cores})
    mask=sum(1<<i for i in cores);job=k.CreateJobObjectW(None,None)
    if not job:raise c.WinError(c.get_last_error())
    limits=Extended();limits.BasicLimitInformation.LimitFlags=0x2000|0x200|0x10
    limits.BasicLimitInformation.Affinity=mask
    limits.JobMemoryLimit=job_memory_mib(protocol,os.environ)*1024**2
    if not k.SetInformationJobObject(job,9,c.byref(limits),c.sizeof(limits)):raise c.WinError(c.get_last_error())
    if not k.AssignProcessToJobObject(job,process):raise c.WinError(c.get_last_error())
    if not k.SetProcessAffinityMask(process,mask):raise c.WinError(c.get_last_error())
    os.environ['SPIRE_TARGET_CPUS']=str(len(cores));os.environ['SPIRE_MEMORY_BUDGET_MIB']=str(limits.JobMemoryLimit//1024**2)
    begun=time.perf_counter();active_begun=active_counter();done=threading.Event()
    from spire_exact.planning.runtime_metrics import configure
    def live_metrics():
        info=Accounting();actual=Extended()
        k.QueryInformationJobObject(job,1,c.byref(info),c.sizeof(info),None)
        k.QueryInformationJobObject(job,9,c.byref(actual),c.sizeof(actual),None)
        return {'job_metrics_available':True,'job_cpu_seconds':(info.TotalUserTime+info.TotalKernelTime)/10_000_000,
                'job_wall_seconds':time.perf_counter()-begun,'peak_job_commit_bytes':actual.PeakJobMemoryUsed,
                **({'job_active_seconds':active_counter()-active_begun,'job_paused_seconds':paused_seconds(),'budget_clock':'pause_excluded'} if os.environ.get('SPIRE_PAUSE_LEDGER') else {})}
    configure(live_metrics)
    events=out.parent/(out.name+'-events.jsonl')
    def hook(kind,**fields):
        with events.open('a',encoding='utf-8')as f:f.write(json.dumps({'event':kind,'wall_seconds':time.perf_counter()-begun,**fields})+'\n')
    hook('started',pid=os.getpid(),protocol=protocol,cpus=cores,efficiency_class=efficiency_class,
         logical_cpu_count=logical_cpus,physical_core_count=physical_count,smt_used=physical_count<logical_cpus)
    def record(timeout=False,storage_limit=False):
        actual=Extended();usage=Accounting()
        io=AccountingIO()
        k.QueryInformationJobObject(job,9,c.byref(actual),c.sizeof(actual),None)
        k.QueryInformationJobObject(job,1,c.byref(usage),c.sizeof(usage),None)
        k.QueryInformationJobObject(job,8,c.byref(io),c.sizeof(io),None)
        data={'enforced':True,'cpu_affinity':cores,'workload_limit_bytes':limits.JobMemoryLimit,
          'logical_cpu_count':logical_cpus,'physical_core_count':physical_count,'smt_used':physical_count<logical_cpus,
          'os_reserve_bytes':2*1024**3,'peak_job_commit_bytes':actual.PeakJobMemoryUsed,
          'peak_process_commit_bytes':actual.PeakProcessMemoryUsed,
          'job_cpu_seconds':(usage.TotalUserTime+usage.TotalKernelTime)/10_000_000,
          'cpu_accounting_is_utilization':False,
          'job_processes':usage.TotalProcesses,'wall_seconds':time.perf_counter()-begun,
          'wall_limit_seconds':wall_limit,'protocol':protocol,'efficiency_class':efficiency_class,'hard_timeout':timeout,'storage_limit':storage_limit,
          'job_write_transfer_bytes':io.IoInfo.WriteTransferCount,'job_write_cap_bytes':JOB_WRITE_CAP}
        if os.environ.get('SPIRE_RESOURCE_OVERRIDE_NOTE'):
            data['resource_protocol_override']=os.environ['SPIRE_RESOURCE_OVERRIDE_NOTE']
        if os.environ.get('SPIRE_PAUSE_LEDGER'):
            data.update(active_seconds=active_counter()-active_begun,paused_seconds=paused_seconds(),budget_clock='pause_excluded')
        telemetry.write_text(json.dumps(data,indent=2)+'\n')
    def watchdog():
        reported=False
        while not done.wait(1):
            io=AccountingIO()
            if not k.QueryInformationJobObject(job,8,c.byref(io),c.sizeof(io),None):
                record(storage_limit=True);k.TerminateJobObject(job,125);return
            if io.IoInfo.WriteTransferCount>=JOB_WRITE_CAP:
                record(storage_limit=True);hook('failed',reason='STORAGE_LIMIT');k.TerminateJobObject(job,125);return
            if not reported and a10 and active_counter()-active_begun>=9000:
                record();hook('report_point_150min');reported=True
            if active_counter()-active_begun>=wall_limit:
                record(True);hook('censored',reason='WALL_SAFETY_CAP');k.TerminateJobObject(job,124);return
    threading.Thread(target=watchdog,daemon=True).start()
    try:
        sys.argv=['spire_exact',*args];runpy.run_module('spire_exact',run_name='__main__')
    finally:
        done.set();record()
        hook('process_finished',result_exists=(out/'result.json').exists())
        active_marker.unlink(missing_ok=True)
    # OS closes the job at process exit; never close it while still doing work.

if __name__=='__main__':main()
