"""Finish the actual native post-final-boss transition, then replay all actions.

The original scoped diagnostic stopped at a live map, not native OnEnded(true).
This follow-up cannot count as an independent fresh planner validation seed.
"""
from pathlib import Path
import ctypes as c,json,os,sys
ROOT=Path(__file__).resolve().parents[1];FROZEN=ROOT/'experiments/frozen-i029';sys.path.insert(0,str(FROZEN))
from spire_exact.planning.io import read_json,write_json
from spire_exact.planning.pool import NativePool
from spire_exact.planning.resources import ResourcePlan
from spire_exact.mode1 import context,check_winning_replay,is_winning_candidate
from tools.cpu_topology import inventory,homogeneous_cpus

def main():
    source=FROZEN/'experiments/iteration-026/l2-preparation-baseline/cases/act-3-target-49-root-07-repeat-0/eval-0029-macro_failure'
    out=ROOT/'experiments/iteration-026/final-gate-completion';out.mkdir(parents=True,exist_ok=False)
    old=read_json(source/'data/decision.json');original=read_json(source/'request.json')
    cpus,_=homogeneous_cpus(inventory(),8,efficiency_class=0)
    k=c.WinDLL('kernel32',use_last_error=True);k.GetCurrentProcess.restype=c.c_void_p;k.SetProcessAffinityMask.argtypes=[c.c_void_p,c.c_size_t]
    if not k.SetProcessAffinityMask(k.GetCurrentProcess(),sum(1<<i for i in cpus)):raise c.WinError(c.get_last_error())
    os.environ.update(SPIRE_TARGET_CPUS='8',SPIRE_MEMORY_BUDGET_MIB='4096')
    req={k:original[k]for k in ('seed','character','ascension','unlocks')}
    req.update(history=old['trace'],generate_candidate=True,max_decisions=len(old['trace'])+64,
               policy_seed=original['policy_seed'],capture_checkpoints=False,low_io=True,event_driven_settle=True)
    write_json(out/'manifest.json',{'source':str(source),'original_status':old['status'],'original_reason':old['reason'],
        'original_was_native_win':is_winning_candidate(old),'cpu_set':cpus,'counts_as_fresh_planner_win':False,
        'scope':'Complete actual end-run transition after a native search-produced post-boss boundary'})
    with NativePool(FROZEN/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64',out/'workers',ResourcePlan.detect(1,1,1536,1024))as pool:
        result,identity=pool.run(req,out/'finish-native',600,fresh=True)
        report={'complete':True,'whole_run_verified':False,'status':result['status'],'reason':result.get('reason'),
                'original_actions':len(old['trace']),'final_actions':len(result['trace']),'counts_as_fresh_planner_win':False}
        if is_winning_candidate(result):
            replay_req={k:original[k]for k in ('seed','character','ascension','unlocks')}
            replay_req.update(history=result['trace'],generate_candidate=False,capture_checkpoints=False,
                              expected_evidence=result['decision_evidence'],low_io=True,event_driven_settle=True)
            replay,other=pool.run(replay_req,out/'independent-full-replay',600,fresh=True)
            ctx=context(original['seed'],original['character'],original['ascension'],original['unlocks'])
            certificate=check_winning_replay(result,replay,{'context':ctx,'native':identity},{'context':ctx,'native':other})
            write_json(out/'certificate.json',certificate)
            write_json(out/'winning-route.json',{'context':ctx,'trace':result['trace']})
            report.update(whole_run_verified=True,value=replay['value'],certificate=certificate,
                          appended_actions=result['trace'][len(old['trace']):])
        write_json(out/'report.json',report)
        print(json.dumps({k:v for k,v in report.items()if k!='certificate'}),flush=True)

if __name__=='__main__':main()
