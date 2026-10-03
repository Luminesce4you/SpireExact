"""Native counterexample: opening multi-remove must retain basic defense."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.mode1 import context,NativeCampaignBackend
from spire_exact.native import export_native
from spire_exact.planning.io import read_json,write_json
data=ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
out=ROOT/'experiments/iteration-006/deck-regression';out.mkdir(parents=True,exist_ok=False)
ctx=context('0','IRONCLAD',0,'all');backend=NativeCampaignBackend(ctx,out,data,'combatsolver')
backend.advisor.update(budget_ms=400,boss_budget_ms=1600,dop=1,reuse_continuations=True)
args={k:ctx[k] for k in ['seed','character','ascension','unlocks']}
export_native('replay',out/'candidate',data,history=[],generate_candidate=True,max_decisions=300,
    advisor=backend.advisor,timeout_seconds=150,**args)
c=read_json(out/'candidate/data/decision.json')
removals=[]
for action,e in zip(c['trace'],c['decision_evidence']):
    obs=e['observation']
    if obs.get('floor')==1 and (obs.get('selection')or{}).get('purpose')=='Remove':
        removals.append([obs['deck'][i]['id'] for i in action['indices']])
export_native('replay',out/'replay',data,history=c['trace'],generate_candidate=False,
    expected_evidence=c['decision_evidence'],timeout_seconds=120,**args)
r=read_json(out/'replay/data/decision.json')
passed=bool(removals) and all(sum(card.startswith('DEFEND') for card in selection)<2 for selection in removals) and r['reason'] is None and c['trace']==r['trace'] and c['decision_evidence']==r['decision_evidence']
report={'passed':passed,'opening_removals':removals,'floor':c['observation']['floor'],
        'candidate_status':c['status'],'actions':len(c['trace']),'counters':c['performance']['counters'],
        'replay_status':r['status'],'replay_reason':r['reason'],'normal_godot_verified':False}
write_json(out/'report.json',report);print(report)
raise SystemExit(0 if passed else 1)
