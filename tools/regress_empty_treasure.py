"""Native SilverCrucible empty-chest callback, conserved rewards, and restore."""
from pathlib import Path
import sys,json,os
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.planning.pool import NativePool
from spire_exact.planning.resources import ResourcePlan
from spire_exact.planning.io import read_json,write_json
from spire_exact.canonical import canonical
from tools.experiment_storage import prepare_storage

out=ROOT/'experiments/iteration-018/empty-treasure-regression';out.mkdir(parents=True,exist_ok=False);prepare_storage(out)
data=ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
source=ROOT/'experiments/frozen-i016/experiments/iteration-016/i016-fresh20/seed-1097359681/eval-0051-macro_exploration/data/decision.json'
old=read_json(source);ctx=read_json(source.parents[2]/'result.json')['context']
request={k:ctx[k]for k in ['seed','character','ascension','unlocks']}
request.update(history=old['trace'],generate_candidate=False,capture_checkpoints=True)
checks={}
os.environ['SPIRE_TARGET_CPUS']='8';os.environ['SPIRE_MEMORY_BUDGET_MIB']='6144'
with NativePool(data,out/'workers',ResourcePlan.detect(1,1,1000,1536))as pool:
    fixed,identity=pool.run(request,out/'fixed',90)
    before=old['decision_evidence'][-1]['observation'];after=fixed['observation']
    checks['original_failure_reaches_native_map']=fixed['status']=='DECISION' and fixed['phase']=='map' and fixed.get('reason')is None
    checks['native_empty_callback_once']=fixed['performance']['counters'].get('native_empty_treasure_completed')==1
    for field in ['hp','max_hp','gold','deck','relics','potions','rng']:
        checks['empty_chest_preserves_'+field]=canonical(before[field])==canonical(after[field])
    replay,other=pool.run({**request,'capture_checkpoints':False,'expected_evidence':fixed['decision_evidence']},out/'independent-replay',90,fresh=True)
    checks['independent_replay_full_observation_rng_equal']=canonical(replay['observation'])==canonical(after) and canonical(replay['decision_evidence'])==canonical(fixed['decision_evidence'])
    checkpoints=[c for c in fixed['checkpoints']if c['prefix_length']==len(fixed['trace'])]
    checks['empty_chest_map_checkpoint_exists']=bool(checkpoints)
    if checkpoints:
        action=next(a for a in fixed['actions']if a['kind']=='map')
        follow={**request,'history':fixed['trace']+[action],'capture_checkpoints':False}
        fresh,_=pool.run(follow,out/'fresh-suffix',90,fresh=True)
        restored,_=pool.run({**follow,'checkpoint':checkpoints[-1]['path']},out/'restored-suffix',90,fresh=True)
        checks['empty_chest_checkpoint_suffix_equal']=canonical(fresh['observation'])==canonical(restored['observation']) and canonical(fresh['decision_evidence'])==canonical(restored['decision_evidence'])
    normal_source=ROOT/'experiments/frozen-i007/experiments/iteration-007/i007-fresh20-v2/seed-1497177934/eval-0042-probe_followup/data/decision.json'
    normal=read_json(normal_source);normal_ctx=read_json(normal_source.parents[2]/'result.json')['context']
    index=next(i for i,a in enumerate(normal['trace'])if a['kind']=='open_chest')
    normal_request={k:normal_ctx[k]for k in ['seed','character','ascension','unlocks']}
    normal_request.update(history=normal['trace'][:index+1],generate_candidate=False)
    offered,_=pool.run(normal_request,out/'normal-offer',90,fresh=True)
    relic_actions=[a for a in offered.get('actions',[])if a['kind']=='treasure']
    checks['normal_treasure_still_offers_native_relics']=offered['phase']=='treasure' and bool(relic_actions)
    checks['normal_treasure_not_completed_as_empty']=offered['performance']['counters'].get('native_empty_treasure_completed',0)==0
    if relic_actions:
        acquired,_=pool.run({**normal_request,'history':offered['trace']+[relic_actions[0]]},out/'normal-acquire',90,fresh=True)
        checks['normal_selected_relic_obtained']=len(acquired['observation']['relics'])==len(offered['observation']['relics'])+1
passed=all(checks.values())
write_json(out/'report.json',{'passed':passed,'checks':checks,'source':str(source),
    'game_sha256':identity['game_sha256'],'normal_godot_scene_verified':False})
print(json.dumps({'passed':passed,'checks':checks},indent=2));raise SystemExit(0 if passed else 1)
