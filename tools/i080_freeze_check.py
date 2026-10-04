"""Describe i080 against the immutable i075 gzip baseline; never modify it."""
import argparse,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.experiment import version_hash
from spire_exact.planning.io import read_json,write_json


def inventory(root):
    files={}
    for name in ('native','spire_exact','tools','tests'):
        for path in (root/name).rglob('*'):
            if path.is_file()and path.suffix in('.py','.cs','.csproj')and not {'bin','obj','__pycache__'}&set(path.parts):
                files[path.relative_to(root).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def main():
    p=argparse.ArgumentParser();p.add_argument('--baseline',type=Path,required=True)
    p.add_argument('--candidate',type=Path,default=ROOT);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();baseline=a.baseline.resolve();candidate=a.candidate.resolve()
    if a.out.exists():raise FileExistsError('never overwrite comparison evidence')
    before=inventory(baseline);after=inventory(candidate)
    expected=read_json(baseline/'freeze.json')['source_version'];actual=version_hash(baseline)
    if expected!=actual:raise ValueError('immutable baseline source changed')
    report={'schema':'spire-i080-freeze-comparison/v1','baseline':str(baseline),'candidate':str(candidate),
            'baseline_version':actual,'baseline_unchanged':True,'candidate_version':version_hash(candidate),
            'added':sorted(after.keys()-before.keys()),'removed':sorted(before.keys()-after.keys()),
            'modified':[{'path':name,'before_sha256':before[name],'after_sha256':after[name]}
                        for name in sorted(before.keys()&after.keys())if before[name]!=after[name]],
            'pinned_manifests':{name:hashlib.sha256((candidate/name).read_bytes()).hexdigest()
                                for name in ('upstream.lock.json','LICENSE','THIRD_PARTY_NOTICES.md')},
            'scope':'project source and helpers only; not a behavioral or performance comparison'}
    if any((candidate/name).read_bytes()!=(baseline/name).read_bytes()for name in report['pinned_manifests']):
        raise ValueError('pinned dependency/license manifest changed')
    write_json(a.out,report)
    print(json.dumps({k:report[k]for k in('baseline_unchanged','candidate_version','added','removed')}))
    return 0


if __name__=='__main__':raise SystemExit(main())
