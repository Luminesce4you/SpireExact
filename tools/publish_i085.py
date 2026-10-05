"""Verify a delivered Git bundle and fast-forward the dedicated research branch.

Fetches objects but does not modify the caller's checkout/index. No force push,
no merge of an unexpectedly advanced remote, no embedded credentials. Requires
the user's normal Git authentication and network. Dry-run is the default.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import subprocess

BRANCH='codex/i085-macro-candidate-20261005'
BASE='dec944a41743b83e44f137a140770e16961aa08f'


def git(repo,*args):
    p=subprocess.run(['git','-C',str(repo),*args],capture_output=True,text=True,encoding='utf-8',errors='replace')
    if p.returncode:raise RuntimeError(p.stderr.strip() or p.stdout.strip() or f'git exited {p.returncode}')
    return p.stdout.strip()


def publish(repo,bundle,*,tip,base=BASE,branch=BRANCH,execute=False):
    repo,bundle=Path(repo).resolve(),Path(bundle).resolve()
    if not bundle.is_file():raise ValueError('bundle does not exist')
    if not all(re.fullmatch(r'[0-9a-f]{40}',s) for s in (tip,base)):raise ValueError('exact 40-character commit ids required')
    git(repo,'check-ref-format','refs/heads/'+branch)
    heads=git(repo,'bundle','list-heads',str(bundle)).splitlines()
    expected=f'{tip} refs/heads/{branch}'
    if expected not in heads:raise ValueError('bundle head does not match the supplied commit and branch')
    git(repo,'fetch','origin','refs/heads/'+branch)
    remote=git(repo,'rev-parse','FETCH_HEAD')
    if remote!=base:raise ValueError(f'remote advanced or differs: expected {base}, found {remote}; no push attempted')
    git(repo,'bundle','verify',str(bundle))
    git(repo,'fetch',str(bundle),'refs/heads/'+branch)
    if git(repo,'rev-parse','FETCH_HEAD')!=tip:raise ValueError('fetched bundle tip mismatch')
    git(repo,'merge-base','--is-ancestor',base,tip)
    report={'base':base,'tip':tip,'branch':branch,'executed_push':False,
            'changes':git(repo,'diff','--stat',base,tip),'worktree_modified':False,'force':False}
    if execute:
        report['push_output']=git(repo,'push','--porcelain','origin',f'{tip}:refs/heads/{branch}')
        actual=git(repo,'ls-remote','origin','refs/heads/'+branch).split()[0]
        if actual!=tip:raise RuntimeError('push returned but remote read-back differs')
        report['executed_push']=True;report['verified_remote_tip']=actual
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,required=True);p.add_argument('--bundle',type=Path,required=True)
    p.add_argument('--tip',required=True);p.add_argument('--base',default=BASE);p.add_argument('--branch',default=BRANCH)
    p.add_argument('--execute',action='store_true')
    a=p.parse_args(argv)
    try:r=publish(a.repo,a.bundle,tip=a.tip,base=a.base,branch=a.branch,execute=a.execute)
    except (ValueError,RuntimeError,OSError) as e:p.exit(1,str(e)+'\n')
    print(json.dumps(r,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
