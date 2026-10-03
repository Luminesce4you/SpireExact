"""Windows CPU Set inventory; never infer P/E classes from contiguous indices.

Layout/source: Microsoft SYSTEM_CPU_SET_INFORMATION and
GetSystemCpuSetInformation documentation. Larger EfficiencyClass means faster,
less efficient processors; labels are recorded without guessing CPU marketing names.
"""
import ctypes as c
from ctypes import wintypes as w
from pathlib import Path
import argparse,json,os,struct

def decode_cpu_sets(data):
    rows=[];offset=0
    while offset<len(data):
        if len(data)-offset<8:raise ValueError('truncated CPU Set header')
        size,kind=struct.unpack_from('<II',data,offset)
        if size<8 or offset+size>len(data):raise ValueError('invalid CPU Set size')
        if kind==0:
            if size<32:raise ValueError('truncated CPU Set record')
            ident,group,logical,core,cache,numa,efficiency,flags=struct.unpack_from('<IH6B',data,offset+8)
            rows.append({'id':ident,'group':group,'logical_cpu':logical,'core_index':core,
                         'last_level_cache':cache,'numa_node':numa,'efficiency_class':efficiency,
                         'parked':bool(flags&1),'allocated':bool(flags&2),'allocated_to_process':bool(flags&4)})
        offset+=size
    return rows

def inventory():
    if os.name!='nt':raise RuntimeError('CPU Set inventory requires Windows')
    k=c.WinDLL('kernel32',use_last_error=True)
    api=k.GetSystemCpuSetInformation
    api.argtypes=[c.c_void_p,w.ULONG,c.POINTER(w.ULONG),w.HANDLE,w.ULONG];api.restype=w.BOOL
    k.GetCurrentProcess.restype=w.HANDLE
    size=w.ULONG();api(None,0,c.byref(size),k.GetCurrentProcess(),0)
    if not size.value:raise c.WinError(c.get_last_error())
    buf=c.create_string_buffer(size.value)
    if not api(buf,size.value,c.byref(size),k.GetCurrentProcess(),0):raise c.WinError(c.get_last_error())
    return decode_cpu_sets(buf.raw[:size.value])

def homogeneous_cpus(rows,count=8,efficiency_class=None):
    classes=sorted({r['efficiency_class']for r in rows},reverse=True)
    if efficiency_class is not None:classes=[efficiency_class]
    for cls in classes:
        candidates=[r for r in rows if r['group']==0 and r['efficiency_class']==cls and
                    (not r['allocated'] or r['allocated_to_process'])]
        cores={}
        for r in sorted(candidates,key=lambda r:r['logical_cpu']):cores.setdefault(r['core_index'],[]).append(r['logical_cpu'])
        ordered=[cpus[level]for level in range(max(map(len,cores.values()),default=0))for _,cpus in sorted(cores.items())if level<len(cpus)]
        if len(ordered)>=count:return ordered[:count],cls
    raise ValueError('No single efficiency class has the requested available logical CPUs')

def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    rows=inventory();cpus,cls=homogeneous_cpus(rows)
    report={'source':'GetSystemCpuSetInformation','cpu_sets':rows,'default_8_cpus':cpus,
            'default_efficiency_class':cls,'documentation':'https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-system_cpu_set_information'}
    a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'classes':{str(k):[r['logical_cpu']for r in rows if r['efficiency_class']==k]for k in sorted({r['efficiency_class']for r in rows})},'default_8_cpus':cpus}))

if __name__=='__main__':main()
