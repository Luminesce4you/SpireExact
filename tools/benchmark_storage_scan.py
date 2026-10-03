"""Read-only ABBA comparison of old and fresh-scandir byte inventory."""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.rolling_storage import walk,linked,logical_bytes


def reference(root):
    total=0
    for base,_,files in walk(root):
        for name in files:
            path=base/name
            if linked(path):continue
            try:total+=path.stat().st_size
            except FileNotFoundError:pass
    return total


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    if a.out.exists():raise SystemExit('Fresh report required')
    from tools.fight_bench import _affinity
    _affinity('e')
    rows=[]
    for name,fn in [('reference',reference),('scandir',logical_bytes),('scandir',logical_bytes),('reference',reference)]:
        start=time.perf_counter();count=fn(a.root.resolve());elapsed=time.perf_counter()-start
        rows.append({'method':name,'logical_bytes':count,'wall_seconds':elapsed})
    report={'root':str(a.root.resolve()),'rows':rows,'identical_bytes':len({r['logical_bytes']for r in rows})==1,
            'speed_ratio':sum(r['wall_seconds']for r in rows if r['method']=='reference')/sum(r['wall_seconds']for r in rows if r['method']=='scandir'),
            'scope':'read-only inventory component; no game or search changes'}
    a.out.write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report))
    if not report['identical_bytes']:raise SystemExit(1)


if __name__=='__main__':main()
