"""Audit direct card effects versus linked native power callbacks; no gameplay mutation."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.native import export_native
from spire_exact.planning.io import read_json,write_json
out=ROOT/'experiments/iteration-010/power-metadata';out.mkdir(parents=True,exist_ok=False)
data=ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
export_native('effect_metadata',out/'native',data,cards=['FEEL_NO_PAIN','DARK_EMBRACE','OFFERING','STRIKE_IRONCLAD'],timeout_seconds=90)
rows={r['card']:r for r in read_json(out/'native/data/effect-metadata.json')}
checks={}
for name in ['FEEL_NO_PAIN','DARK_EMBRACE']:
    checks[name+'_direct_does_not_contain_power_hook']='AfterCardExhausted' not in rows[name]['direct']
    checks[name+'_linked_contains_power_hook']='AfterCardExhausted' in rows[name]['linked']
    checks[name+'_conditional_not_immediate_draw']=rows[name]['capability']['Draw']==0
checks['Offering_is_exhaust_source']=rows['OFFERING']['capability']['Exhaust']>0
checks['Strike_is_not_exhaust_payoff']=rows['STRIKE_IRONCLAD']['capability']['ExhaustPayoff']==0
passed=all(checks.values());write_json(out/'report.json',{'passed':passed,'checks':checks,'normal_gameplay_verified':False})
print({'passed':passed,'checks':checks});raise SystemExit(0 if passed else 1)
