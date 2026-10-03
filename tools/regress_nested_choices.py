"""Exercise planned native nested card selections and independently replay them."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.mode1 import context,NativeCampaignBackend
from spire_exact.native import export_native
from spire_exact.planning.io import read_json,write_json
data=ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
out=ROOT/'experiments/iteration-003/nested-regression';out.mkdir(parents=True,exist_ok=False)
ctx=context('0','IRONCLAD',0,'all');backend=NativeCampaignBackend(ctx,out,data,'combatsolver')
backend.advisor.update(budget_ms=400,boss_budget_ms=1600,dop=1,reuse_continuations=True)
args={k:ctx[k] for k in ['seed','character','ascension','unlocks']}
export_native('replay',out/'candidate',data,history=[],generate_candidate=True,max_decisions=240,
    advisor=backend.advisor,timeout_seconds=120,**args)
c=read_json(out/'candidate/data/decision.json')
export_native('replay',out/'replay',data,history=c['trace'],generate_candidate=False,
    expected_evidence=c['decision_evidence'],timeout_seconds=120,**args)
r=read_json(out/'replay/data/decision.json');metrics=c['advisor_metrics']['counters']
passed=metrics.get('selection_hit',0)>0 and r['reason'] is None and c['trace']==r['trace'] and c['decision_evidence']==r['decision_evidence']
report={'passed':passed,'advisor':metrics,'floor':c['observation']['floor'],'candidate_status':c['status'],
        'replay_status':r['status'],'replay_reason':r['reason'],'normal_godot_verified':False}
write_json(out/'report.json',report);print(report)
raise SystemExit(0 if passed else 1)
