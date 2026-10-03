"""Known failing validation paths are now development regressions, never answers."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.native import export_native
from spire_exact.planning.io import read_json,write_json
old=ROOT/'experiments/frozen-i001/experiments/iteration-001/i001-fresh20'
out=ROOT/(sys.argv[1] if len(sys.argv)>1 else 'experiments/iteration-003/ui-regressions')
data=ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
cases=[('trial','217680771','eval-0028-deepen'),('rocket','1523482897','eval-0052-deepen'),
       ('crusher','2044054000','eval-0008-lookahead'),('nexus','793010028','eval-0007-combat_probe'),
       ('jungle','1362376870','eval-0000-baseline')]
rows=[]
for name,seed,label in cases:
    source=old/('seed-'+seed)/label
    req=read_json(source/'request.json');failed=read_json(source/'data/decision.json')
    args={k:req[k] for k in ['seed','character','ascension','unlocks']}
    export_native('replay',out/name,data,history=failed['trace'],generate_candidate=False,timeout_seconds=120,**args)
    actual=read_json(out/name/'data/decision.json')
    ok=actual['reason'] is None and actual['consumed']==len(failed['trace'])
    rows.append({'case':name,'passed':ok,'consumed':actual['consumed'],'status':actual['status'],
        'reason':actual['reason'],'observation':actual['observation']})
    write_json(out/'report.json',{'successful':all(r['passed'] for r in rows),'checks':rows,'normal_godot_verified':False})
    print({k:rows[-1][k] for k in ['case','passed','status','reason']},flush=True)
raise SystemExit(0 if all(r['passed'] for r in rows) else 1)
