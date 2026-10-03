"""Re-execute the exact pre-failure histories from the previous diagnosis.

These are development-only regression prefixes, never planner answers.
"""
from pathlib import Path
import json,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.native import export_native
from spire_exact.planning.io import read_json,write_json
old=ROOT.parent/'runs/P5-v0.3.0-multiseed-20260929/STS2/outputs/multiseed-10'
out=ROOT/'experiments/iteration-001/event-regression'
data=ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
rows=[]
for seed in ['0','2']:
    source=old/('seed-'+seed)/'eval-0000-baseline'
    request=read_json(source/'request.json')
    history=[json.loads(line) for line in (source/'data/trace.jsonl').read_text().splitlines() if line.strip()]
    params={k:request[k] for k in ['seed','character','ascension','unlocks']}
    export_native('replay',out/('seed-'+seed),data,history=history,generate_candidate=False,timeout_seconds=90,**params)
    result=read_json(out/('seed-'+seed)/'data/decision.json')
    passed=result['status']=='DECISION' and result['consumed']==len(history) and result.get('reason') is None
    rows.append({'seed':seed,'passed':passed,'decisions':len(history),'phase':result['phase'],'status':result['status'],
        'observation':result['observation'],'reason':result.get('reason')})
    write_json(out/'report.json',{'successful':all(r['passed'] for r in rows),'normal_godot_verified':False,'checks':rows})
    print({'seed':seed,'passed':passed,'status':result['status'],'reason':result.get('reason')},flush=True)
if not all(r['passed'] for r in rows):raise SystemExit(1)
