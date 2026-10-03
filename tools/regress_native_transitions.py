"""Known native event: inspect nested choice, native cost, replay and RNG equality."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.mode1 import context
from spire_exact.canonical import canonical
from spire_exact.planning.pool import NativePool
from spire_exact.planning.resources import ResourcePlan
from spire_exact.planning.search import SearchConfig,Evaluator
from spire_exact.planning.transitions import NativeTransitionOracle
from spire_exact.planning.io import read_json,write_json

out=ROOT/'experiments/iteration-009/transition-regression';out.mkdir(parents=True,exist_ok=False)
data=ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
ctx=context('0','IRONCLAD',0,'all');plan=ResourcePlan.detect(1,1,900,1024)
with NativePool(data,out/'workers',plan) as pool:
    ev=Evaluator(pool,ctx,out,SearchConfig(evaluations=20,seconds=120),None);oracle=NativeTransitionOracle(ev)
    initial=oracle.initial()
    action=next(a for a in initial['actions'] if a.get('key','').endswith('PRECARIOUS_SHEARS'))
    pending=oracle.apply(initial,action)
    choice=next(a for a in pending['state']['actions'] if a.get('indices')==[0,1])
    completed=oracle.apply(pending['state'],choice)
    request={k:ctx[k] for k in ['seed','character','ascension','unlocks']}
    request.update(history=completed['state']['trace'],generate_candidate=False,
                   expected_evidence=completed['state']['decision_evidence'])
    replay,identity=pool.run(request,out/'independent-replay',60,fresh=True)
    passed=(initial['phase']=='event' and pending['state']['phase']=='select_cards'
        and pending['pending_native_card_selection'] and completed['state']['phase']=='map'
        and completed['observed_delta']['hp']=='-16'
        and len(completed['observed_delta']['deck']['removed'])==2
        and replay.get('reason') is None
        and canonical(replay['observation'])==canonical(completed['state']['observation'])
        and canonical(replay['decision_evidence'])==canonical(completed['state']['decision_evidence']))
    report={'passed':passed,'initial_phase':initial['phase'],'nested_phase':pending['state']['phase'],
        'final_phase':completed['state']['phase'],'delta':completed['observed_delta'],
        'full_observation_and_rng_equal_after_independent_replay':passed,'normal_godot_verified':False}
    write_json(out/'report.json',report);print(report)
raise SystemExit(0 if passed else 1)
