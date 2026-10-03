"""Audit root portfolio proposals against real DLL menus and fresh replay."""
from pathlib import Path
import ctypes, os, sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.mode1 import context
from spire_exact.canonical import canonical
from spire_exact.planning.pool import NativePool
from spire_exact.planning.resources import ResourcePlan
from spire_exact.planning.search import SearchConfig,Evaluator
from spire_exact.planning.root_portfolio import root_requests
from spire_exact.planning.io import write_json

out=ROOT/'experiments/iteration-019/root-regression';out.mkdir(parents=True,exist_ok=False)
os.environ['SPIRE_TARGET_CPUS']='8';os.environ['SPIRE_MEMORY_BUDGET_MIB']='6144'
if os.name=='nt':
    k=ctypes.WinDLL('kernel32',use_last_error=True);k.GetCurrentProcess.restype=ctypes.c_void_p
    k.SetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
    if not k.SetProcessAffinityMask(k.GetCurrentProcess(),sum(1<<i for i in range(16,24))):raise ctypes.WinError(ctypes.get_last_error())
data=ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
checks={}
with NativePool(data,out/'workers',ResourcePlan.detect(2,1,1000,1536))as pool:
    ev=Evaluator(pool,context('0','IRONCLAD',0,'all'),out,SearchConfig(evaluations=20,seconds=90))
    req=ev.request([]);req['stop_at_strategic_decision']=True
    initial=ev.batch([{'kind':'root_menu','request':req}])[0][1]
    specs,deferred=root_requests(initial,ev,4)
    checks['fresh_native_menu']=initial['status']=='DECISION' and not initial['trace']
    checks['multiple_real_opening_choices']=len(specs)>1
    checks['only_native_actions']=all(canonical(s['request']['history'][0]) in {canonical(a)for a in initial['actions']}for s in specs)
    for s in specs:s['request']['stop_at_strategic_decision']=True
    for index,(spec,state,label)in enumerate(ev.batch(specs)):
        checks[f'option_{index}_executed']=state['status']=='DECISION' and state['trace'][:1]==spec['request']['history'] and state.get('reason')is None
        replay_req={k:ev.ctx[k]for k in ['seed','character','ascension','unlocks']}
        replay_req.update(history=state['trace'],generate_candidate=False,expected_evidence=state['decision_evidence'])
        replay,identity=pool.run(replay_req,out/f'independent-{index}',30,fresh=True)
        checks[f'option_{index}_replay_equal']=canonical(replay['observation'])==canonical(state['observation']) and canonical(replay['decision_evidence'])==canonical(state['decision_evidence'])
    write_json(out/'report.json',{'passed':all(checks.values()),'checks':checks,'deferred_native_options':deferred,
        'native_initial_actions':initial['actions'],'identity':identity,'normal_godot_verified':False})
print(checks)
raise SystemExit(0 if all(checks.values()) else 1)
