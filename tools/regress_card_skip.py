"""Actual native skip-navigation regression, then replay without an advisor."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.mode1 import context,NativeCampaignBackend
from spire_exact.native import export_native
from spire_exact.planning.io import read_json,write_json
from spire_exact.canonical import canonical

data=ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
out=ROOT/'experiments/iteration-002/skip-regression'
out.mkdir(parents=True,exist_ok=False)
ctx=context('0','IRONCLAD',0,'all')
backend=NativeCampaignBackend(ctx,out,data,'combatsolver')
params={k:ctx[k] for k in ['seed','character','ascension','unlocks']}
export_native('replay',out/'candidate',data,history=[],generate_candidate=True,max_decisions=180,
              policy_seed=0,advisor=backend.advisor,timeout_seconds=90,**params)
candidate=read_json(out/'candidate/data/decision.json')
export_native('replay',out/'replay',data,history=candidate['trace'],generate_candidate=False,
              expected_evidence=candidate['decision_evidence'],timeout_seconds=90,**params)
replay=read_json(out/'replay/data/decision.json')
skips=candidate['performance']['counters'].get('skipped_rewards',0)
passed=(skips>0 and candidate['observation']['floor']>4 and replay['reason'] is None
        and canonical(candidate['trace'])==canonical(replay['trace'])
        and canonical(candidate['decision_evidence'])==canonical(replay['decision_evidence']))
report={'passed':passed,'skips':skips,'floor':candidate['observation']['floor'],
        'actions':len(candidate['trace']),'replay_status':replay['status'],'replay_reason':replay['reason'],
        'normal_godot_verified':False,'whole_run_victory':False}
write_json(out/'report.json',report);print(report)
raise SystemExit(0 if passed else 1)
