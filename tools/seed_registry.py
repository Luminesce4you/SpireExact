"""Shared, process-locked seed reservation across frozen experiment workspaces."""
from contextlib import contextmanager
from pathlib import Path
import json,os

@contextmanager
def locked(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as f:
        f.seek(0,2)
        if f.tell()==0:f.write(b'0');f.flush()
        f.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(f.fileno(),msvcrt.LK_LOCK,1)
        else:
            import fcntl
            fcntl.flock(f.fileno(),fcntl.LOCK_EX)
        try:yield
        finally:
            f.seek(0)
            if os.name=='nt':msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(f.fileno(),fcntl.LOCK_UN)

def reserve(root,run_id,version,kind,count,explicit,seeder):
    from spire_exact.planning.io import read_json,write_json
    root=Path(root)
    owner=root.parent.parent if root.parent.name=='experiments' and root.name.startswith('frozen-') else root
    ledger=owner/'experiments/seed-ledger.json'
    with locked(ledger.with_suffix('.lock')):
        used=read_json(ledger) if ledger.exists() else {'reserved':['0','1','2','42','43','100','101','102','103','104'],'runs':[]}
        known={r['run_id'] for r in used['runs']}
        for path in (owner/'experiments').glob('frozen-*/experiments/seed-ledger.json'):
            for run in read_json(path)['runs']:
                if run['run_id'] not in known:used['runs'].append(run);known.add(run['run_id'])
        if run_id in known:raise ValueError('Run id already reserved in shared ledger')
        if explicit and kind!='smoke':raise ValueError('Development seeds may only be smoke/regression')
        excluded=set(used['reserved'])|{s for r in used['runs'] for s in r['seeds']}
        seeds=explicit or seeder(run_id,count,excluded)
        used['runs'].append({'run_id':run_id,'version':version,'kind':kind,'seeds':seeds})
        write_json(ledger,used)
        if root!=owner:write_json(root/'experiments/seed-ledger.json',used)
    return seeds,str(ledger)
