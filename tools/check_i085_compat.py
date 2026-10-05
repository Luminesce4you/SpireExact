"""Compare an actual i082 reference checkout with this candidate, without DLLs.

Independent child processes prevent Python import caches mixing the versions.
This is finite fixture coverage, not a proof of all native scheduling behavior.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
CHILD=r'''
import json,sys
from pathlib import Path
root=Path(sys.argv[1]);sys.path[:0]=[str(root),str(root/'tests')]
from tools.run_release_source import settings,effective_parameters
from spire_exact.canonical import digest
from test_focus_search import run
profiles={name:effective_parameters(['--feature-profile',name,'--out','unused']) for name in ('legacy','i075-final','i081','i082')}
paths=[]
for seed in (0,1,8505):
 for aware in (False,True):
  for extra in ({},{'root_policies':('pick','elo'),'root_async':True,'root_round':7},{'focus_cluster_cap':2,'focus_stall':4}):
   _,requests=run(evaluations=80,solver_seed=seed,policy_aware=aware,**extra)
   paths.append({'solver_seed':seed,'policy_aware':aware,'extra':extra,'requests':digest([[k,r] for k,r in requests]),'count':len(requests)})
print(json.dumps({'profiles':profiles,'i082_argv':settings('101',30,7,271828,'i082'),'toy_paths':paths},sort_keys=True))
'''


def snapshot(root):
    command=[sys.executable,'-c',CHILD,str(root)]
    result=subprocess.run(command,cwd=root,capture_output=True,text=True,encoding='utf-8',timeout=90)
    if result.returncode:raise ValueError(f'reference/current snapshot failed: {result.stderr}')
    return json.loads(result.stdout)


def compare(reference):
    old,new=snapshot(reference),snapshot(ROOT)
    profile_diffs={name:{k:[v,new['profiles'][name].get(k)] for k,v in values.items()
                        if new['profiles'][name].get(k)!=v} for name,values in old['profiles'].items()}
    # Additive flags are explicitly reported, not silently thrown away.
    additions={name:sorted(set(new['profiles'][name])-set(values)) for name,values in old['profiles'].items()}
    native_diffs=[]
    checked=[]
    files=[p.relative_to(reference) for p in (reference/'native').rglob('*') if p.is_file() and p.suffix in ('.cs','.csproj')]
    files += [Path('spire_exact/mode1.py'),Path('spire_exact/canonical.py'),Path('upstream.lock.json'),
              Path('tools/solver_patches.py'),Path('tools/source_dependencies.lock.json')]
    for relative in files:
        a,b=reference/relative,ROOT/relative
        if not a.exists():continue
        checked.append(relative.as_posix())
        if not b.exists() or a.read_bytes()!=b.read_bytes():native_diffs.append(relative.as_posix())
    return {'schema':'spire-i085-compatibility/v1','reference_root':str(reference),
        'current_root':str(ROOT),'legacy_profile_differences':profile_diffs,'additive_parameters':additions,
        'i082_launcher_argv_equal':old['i082_argv']==new['i082_argv'],
        'fixture_paths_equal':old['toy_paths']==new['toy_paths'],'fixture_paths_compared':len(old['toy_paths']),
        'ordinary_requests_compared':sum(v['count'] for v in old['toy_paths']),
        'native_and_proof_files_checked':checked,'native_or_proof_byte_differences':native_diffs,
        'reference_snapshot_sha256':hashlib.sha256(json.dumps(old,sort_keys=True).encode()).hexdigest(),
        'current_snapshot_sha256':hashlib.sha256(json.dumps(new,sort_keys=True).encode()).hexdigest(),
        'successful':not any(profile_diffs.values()) and old['i082_argv']==new['i082_argv'] and old['toy_paths']==new['toy_paths'] and not native_diffs,
        'allowed_difference':'post-successful-replay verification-timing.json for both controls; extra disabled settings/telemetry keys',
        'native_executed':False,'scope':'independent parser and deterministic toy-scheduler regression, not native equivalence proof'}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--reference-root',required=True,type=Path);p.add_argument('--out',required=True,type=Path)
    a=p.parse_args(argv)
    if a.out.exists():p.error('output exists; preserve old evidence')
    try:record=compare(a.reference_root.resolve())
    except (OSError,ValueError,subprocess.TimeoutExpired) as e:p.error(str(e))
    a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:record[k] for k in ('successful','fixture_paths_compared','ordinary_requests_compared','native_or_proof_byte_differences')}))
    return 0 if record['successful'] else 1


if __name__=='__main__':raise SystemExit(main())
