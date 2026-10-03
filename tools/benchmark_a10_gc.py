"""Counterbalanced native A10 runtime comparison; never fresh planner wins."""
from pathlib import Path
import ctypes as c,json,os,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.planning.io import read_json,write_json
from spire_exact.native import build_host,export_native
from spire_exact.mode1 import NativeCampaignBackend,context
from spire_exact.canonical import canonical
from tools.cpu_topology import inventory,homogeneous_cpus

OUT=ROOT/'experiments/iteration-024/gc-runtime-pairs'
SOURCE=ROOT/'experiments/frozen-i021/experiments/iteration-021/m0-long-dev00-s271828/seed-564940356/eval-0378-macro_route'
ARMS=[('workstation-1',1,False),('workstation-2',2,False),('server-one-heap',2,True)]

def main():
    OUT.mkdir(parents=True,exist_ok=False)
    cpus,cls=homogeneous_cpus(inventory(),8,efficiency_class=0)
    k=c.WinDLL('kernel32',use_last_error=True);k.GetCurrentProcess.restype=c.c_void_p
    k.SetProcessAffinityMask.argtypes=[c.c_void_p,c.c_size_t]
    if not k.SetProcessAffinityMask(k.GetCurrentProcess(),sum(1<<i for i in cpus)):raise c.WinError(c.get_last_error())
    data=ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
    _,_,build=build_host(data)
    original=read_json(SOURCE/'request.json');trajectory=read_json(SOURCE/'data/decision.json')
    ctx=context(original['seed'],original['character'],original['ascension'],original['unlocks'])
    adapter=NativeCampaignBackend(ctx,OUT/'adapter',data,'combatsolver')
    advisor=dict(adapter.advisor,dop=1,nodes=60000,budget_ms=600000,boss_budget_ms=600000,profile='Low',reuse_continuations=True)
    entries={}
    for i,e in enumerate(trajectory['decision_evidence']):
        o=e.get('observation')or{}
        if e['phase']=='combat'and o.get('floor')in(17,33,49):entries.setdefault(o['floor'],i)
    if set(entries)!={17,33,49}:raise RuntimeError('Required true boss entries unavailable')
    write_json(OUT/'manifest.json',{'source':str(SOURCE),'entries':entries,'cpu_set':cpus,'efficiency_class':cls,
        'node_budget':60000,'dop':1,'arms':ARMS,'native_build':build,'counts_as_planner_win':False,
        'concurrent_load':'immutable P-core baseline; timings only compared within this E-core diagnostic'})
    rows=[];semantics={};passed=True
    for floor,index in entries.items():
        for repeat in range(2):
            for name,cpus_count,server in(ARMS if repeat==0 else ARMS[::-1]):
                os.environ.update(DOTNET_PROCESSOR_COUNT=str(cpus_count),DOTNET_gcServer=str(int(server)),
                    DOTNET_GCHeapCount='1',DOTNET_GCNoAffinitize='1',DOTNET_gcConcurrent='1')
                out=OUT/f'floor-{floor}-repeat-{repeat}-{name}'
                req={k:original[k]for k in['seed','character','ascension','unlocks']}
                req.update(history=trajectory['trace'][:index],generate_candidate=True,advisor=advisor,
                    policy_seed=original['policy_seed'],capture_checkpoints=False,event_driven_settle=True,
                    max_decisions=index+200,stop_at_floor=floor,compact=True,low_io=True)
                export_native('replay',out,data,timeout_seconds=900,**req)
                result=read_json(out/'data/decision.json');perf=result['performance']
                searches=(result.get('advisor_metrics')or{}).get('searches',[])
                game=canonical({key:result.get(key)for key in ['status','phase','reason','value','observation','trace','decision_evidence','native_terminal_observed']})
                nodes=sum(s.get('expanded_nodes')or 0 for s in searches)
                baseline=semantics.setdefault(floor,(game,nodes))
                same=baseline==(game,nodes)
                mode_ok=perf['runtime_processor_count']==cpus_count and perf['gc_server']==server
                row={'floor':floor,'repeat':repeat,'arm':name,'same_full_game_and_nodes':same,'runtime_mode_verified':mode_ok,
                     'nodes':nodes,'status':result['status'],'search_calls':len(searches),'wall_us':perf['wall_us'],'cpu_us':perf['cpu_us'],
                     'beam_us':perf['exclusive_stages'].get('beam_search',{}).get('us',0),
                     'gc_pause_ms':sum(s.get('gc_pause_ms',0)for s in searches),'allocated_bytes':sum(s.get('allocated_bytes',0)for s in searches),
                     'peak_working_set_bytes':perf['peak_working_set_bytes'],'gc_configuration':perf['gc_configuration']}
                rows.append(row);passed &=same and mode_ok and bool(searches)
                write_json(OUT/'report.json',{'passed':False,'complete':False,'checks_so_far':passed,'rows':rows})
                print(json.dumps({'event':'runtime_case_completed',**{k:v for k,v in row.items()if k!='gc_configuration'}}),flush=True)
    report={'passed':passed,'complete':True,'rows':rows,'counts_as_planner_win':False}
    report['totals']={name:{key:sum(r[key]for r in rows if r['arm']==name)for key in['wall_us','cpu_us','beam_us','gc_pause_ms']}for name,_,_ in ARMS}
    write_json(OUT/'report.json',report);print(json.dumps({'event':'runtime_benchmark_completed','passed':passed,'totals':report['totals']}),flush=True)
    raise SystemExit(0 if passed else 1)

if __name__=='__main__':main()
