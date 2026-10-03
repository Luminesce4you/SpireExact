"""Replay the actual fatal Ranwid prefix, then execute native event continuation."""
from pathlib import Path
import argparse,json,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact import native
from spire_exact.planning.io import read_json,write_json

p=argparse.ArgumentParser();p.add_argument('--scratch',action='store_true');a=p.parse_args()
out=ROOT/'experiments/iteration-006'/('ranwid-scratch' if a.scratch else 'ranwid-regression')
out.mkdir(parents=True,exist_ok=False)
if a.scratch:native.HOST=ROOT/'experiments/iteration-006/buildcheck/native/SpireNativeHost'
original=ROOT/'experiments/iteration-005/i005-weighted-smoke/seed-0/eval-0014-macro_deck'
request=read_json(original/'request.json')
trace=[json.loads(line) for line in (original/'data/trace.jsonl').read_text(encoding='utf-8').splitlines()]
args={k:request[k] for k in ['seed','character','ascension','unlocks']}
data=ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
native.export_native('replay',out/'event',data,history=trace,generate_candidate=False,timeout_seconds=90,**args)
e=read_json(out/'event/data/decision.json')
actions=e.get('available_actions',e.get('actions',[]))
# Get the menu from the native decision rather than fabricate an event reward.
write_json(out/'event-result.json',e)
if e.get('phase')!='event' or e.get('reason'):
    raise SystemExit('Did not reach the formerly fatal event menu')
native.export_native('replay',out/'continue',data,history=trace,generate_candidate=True,
    max_decisions=len(trace)+4,timeout_seconds=90,**args)
c=read_json(out/'continue/data/decision.json')
native.export_native('replay',out/'replay',data,history=c['trace'],generate_candidate=False,
    expected_evidence=c['decision_evidence'],timeout_seconds=90,**args)
r=read_json(out/'replay/data/decision.json')
passed=len(c['trace'])>len(trace) and r.get('reason') is None and c['trace']==r['trace'] and c['decision_evidence']==r['decision_evidence']
report={'passed':passed,'formerly_fatal_prefix_actions':len(trace),'actions':len(c['trace']),
        'phase_after_failure_point':e['phase'],'floor_after_continuation':c['observation']['floor'],
        'native_status':c['status'],'replay_status':r['status'],'normal_godot_verified':False}
write_json(out/'report.json',report);print(report)
raise SystemExit(0 if passed else 1)
