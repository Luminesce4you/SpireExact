"""Register disjoint A10 DEV/TRAIN panels; do not open a HOLDOUT early."""
from pathlib import Path
import json,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.seed_registry import reserve
from tools.experiment import seed_set,version_hash
from spire_exact.planning.io import read_json,write_json

def main():
    folder=ROOT/'experiments/panels/A10-seed-v2';folder.mkdir(parents=True,exist_ok=True)
    ledger=read_json(ROOT/'experiments/seed-ledger.json')
    known={row['run_id']:row for row in ledger['runs']}
    for role,count in [('DEV',20),('TRAIN',200)]:
        run_id='A10-seed-v2-panel-'+role.lower()
        if run_id in known:
            seeds=known[run_id]['seeds']
            if len(seeds)!=count:raise ValueError('Existing panel size mismatch; never replace its seeds')
            ledger_path=str(ROOT/'experiments/seed-ledger.json')
        else:
            seeds,ledger_path=reserve(ROOT,run_id,version_hash(),role,count,None,seed_set)
        doc={'schema':'spire-panel/v1','protocol':'A10-seed-v2','role':role,'run_id':run_id,
             'character':'IRONCLAD','ascension':10,'unlocks':'all','information':'full',
             'fresh_start':True,'count':count,'seeds':seeds,'seed_registry':ledger_path,
             'training_allowed':role=='TRAIN','holdout_generated':False}
        path=folder/(role.lower()+'.json')
        if path.exists() and read_json(path)!=doc:raise ValueError('Existing panel manifest differs')
        write_json(path,doc)
    dev=read_json(folder/'dev.json');train=read_json(folder/'train.json')
    if set(dev['seeds'])&set(train['seeds']):raise ValueError('Panel overlap')
    print(json.dumps({'DEV':len(dev['seeds']),'TRAIN':len(train['seeds']),'disjoint':True,'repro_test_seeds':dev['seeds'][:2],'HOLDOUT':'not generated'}))

if __name__=='__main__':main()
