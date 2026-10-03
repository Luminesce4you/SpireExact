"""Host/cgroup-aware resource admission, not a promise of hardware utilization."""
from __future__ import annotations
import ctypes
import os
from dataclasses import asdict, dataclass
from pathlib import Path
import math

MIB = 1024**2


def enclosing_job_memory_limit() -> int | None:
    """Read an enforced Windows Job cap, never trust an environment declaration.

    A null Job handle queries the calling process's associated Job. A nested
    outer Job can be tighter; missing/unknown limits deliberately fail closed.
    """
    if os.name != 'nt':
        return None
    from ctypes import wintypes as w
    class Basic(ctypes.Structure):
        _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                    ('flags', w.DWORD), ('min_ws', ctypes.c_size_t), ('max_ws', ctypes.c_size_t),
                    ('active', w.DWORD), ('affinity', ctypes.c_size_t),
                    ('priority', w.DWORD), ('scheduling', w.DWORD)]
    class Extended(ctypes.Structure):
        _fields_ = [('basic', Basic), ('io', ctypes.c_ulonglong * 6),
                    ('process_limit', ctypes.c_size_t), ('job_limit', ctypes.c_size_t),
                    ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    query = kernel.QueryInformationJobObject
    query.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p]
    query.restype = w.BOOL
    info = Extended()
    if not query(None, 9, ctypes.byref(info), ctypes.sizeof(info), None):
        return None
    return int(info.job_limit) if info.basic.flags & 0x200 and info.job_limit else None

def _text(path: str) -> str | None:
    try: return Path(path).read_text().strip()
    except OSError: return None

def process_memory(pid: int) -> int:
    if os.name == 'nt':
        class Counters(ctypes.Structure):
            _fields_ = [('cb',ctypes.c_ulong),('PageFaultCount',ctypes.c_ulong)] + [
                (k,ctypes.c_size_t) for k in ('PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage',
                'QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage')]
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.restype=ctypes.c_void_p
        kernel.CloseHandle.argtypes=[ctypes.c_void_p]
        handle=kernel.OpenProcess(0x410,False,pid)
        if not handle: return 0
        try:
            p=Counters();p.cb=ctypes.sizeof(p)
            dll=ctypes.WinDLL('psapi');dll.GetProcessMemoryInfo.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_ulong]
            return int(p.WorkingSetSize) if dll.GetProcessMemoryInfo(handle,ctypes.byref(p),p.cb) else 0
        finally: kernel.CloseHandle(handle)
    text=_text(f'/proc/{pid}/status') or ''
    for line in text.splitlines():
        if line.startswith('VmRSS:'): return int(line.split()[1])*1024
    return 0

def available_memory() -> int:
    if os.name=='nt':
        class Memory(ctypes.Structure):
            _fields_=[('dwLength',ctypes.c_ulong),('dwMemoryLoad',ctypes.c_ulong)] + [
                (n,ctypes.c_ulonglong) for n in ('total','available','page_total','page_available','virtual_total','virtual_available','extended')]
        s=Memory();s.dwLength=ctypes.sizeof(s)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s)): return int(s.available)
        return 1024*MIB
    values={line.split(':')[0]:int(line.split()[1])*1024 for line in (_text('/proc/meminfo') or '').splitlines()}
    available=values.get('MemAvailable',1024*MIB)
    limit,current=_text('/sys/fs/cgroup/memory.max'),_text('/sys/fs/cgroup/memory.current')
    if limit and limit!='max' and current:
        # cgroup.current includes file cache. Conservatively discount only half of
        # inactive_file (not all file memory); still bounded by host MemAvailable.
        stats={line.split()[0]:int(line.split()[1]) for line in
               (_text('/sys/fs/cgroup/memory.stat') or '').splitlines() if len(line.split())==2}
        reclaimable=min(int(current),stats.get('inactive_file',0))//2
        available=min(available,max(0,int(limit)-int(current)+reclaimable))
    return available

def effective_cpus() -> float:
    cpus=float(len(os.sched_getaffinity(0)) if hasattr(os,'sched_getaffinity') else (os.cpu_count() or 1))
    quota=_text('/sys/fs/cgroup/cpu.max')
    if quota:
        q,p=quota.split()
        if q!='max': cpus=min(cpus,int(q)/int(p))
    if os.environ.get('SPIRE_TARGET_CPUS'):cpus=min(cpus,float(os.environ['SPIRE_TARGET_CPUS']))
    return max(0.1,cpus)

@dataclass(frozen=True)
class ResourcePlan:
    workers: int
    dop: int
    worker_memory_bytes: int
    reserve_bytes: int
    effective_cpus: float
    available_memory_bytes: int
    @classmethod
    def detect(cls,workers: int|None=None,dop: int=1,worker_mib: int=900,reserve_mib: int=512):
        if dop<1 or worker_mib<64 or reserve_mib<0 or (workers is not None and workers<1):
            raise ValueError('invalid resource budget')
        cpu,mem=effective_cpus(),available_memory()
        if os.environ.get('SPIRE_MEMORY_BUDGET_MIB'):
            mem=min(mem,int(os.environ['SPIRE_MEMORY_BUDGET_MIB'])*MIB)
        # Automatic sizing leaves a coordinator slot. Explicit demand may use
        # every admitted logical CPU, but never exceed CPUs / DOP or memory.
        cpu_budget=max(1,math.floor(cpu-(1 if workers is None else 0)))
        dop=min(dop,cpu_budget)
        cpu_capacity=max(1,cpu_budget//dop)
        memory_capacity=math.floor(max(0,mem-reserve_mib*MIB)/(worker_mib*MIB))
        if memory_capacity<1: raise MemoryError('insufficient available memory for one worker plus reserve')
        capacity=min(cpu_capacity,memory_capacity,32)
        n=capacity if workers is None else min(workers,capacity)
        # Explicit demand is clamped, never silently oversubscribed.
        return cls(n,dop,worker_mib*MIB,reserve_mib*MIB,cpu,mem)
    def as_dict(self):return asdict(self)
