"""Copy an immutable validation workspace, preserving pinned source/license files."""
from pathlib import Path
import argparse,shutil,sys,os,subprocess
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.planning.io import read_json,write_json
from experiment import version_hash
from tools.rolling_storage import config,admission

def main():
    p=argparse.ArgumentParser();p.add_argument('name');p.add_argument('--artifact-root',type=Path);a=p.parse_args()
    policy=config(ROOT)
    if a.artifact_root is None and policy:a.artifact_root=Path(policy['artifact_roots'][0])
    admission(ROOT)
    out=ROOT/'experiments'/a.name
    if out.exists():raise SystemExit('Frozen workspace already exists')
    out.mkdir()
    ignore=shutil.ignore_patterns('bin','obj','__pycache__','.godot','*.pyc','dotnet-sdk*.tar.gz')
    for name in ['native','spire_exact','tools','tests','examples','cloud','runtime','vendor']:
        if (ROOT/name).is_dir():shutil.copytree(ROOT/name,out/name,ignore=ignore)
    # The advisor is a pinned, separately built dependency. Preserve its tested
    # binaries alongside its source; each run fingerprints those binaries again.
    for name in ['vendor/CombatSolver/.godot/mono/temp/bin/Release',
                 'vendor/CombatSolver/tools/OfflineSearchHarness/bin/Release/net9.0']:
        if (ROOT/name).is_dir():shutil.copytree(ROOT/name,out/name)
    for name in ['NuGet.Config','Directory.Build.props','AGENTS.md','.gitignore','PROGRESS.md',
                 'LICENSE','THIRD_PARTY_NOTICES.md','upstream.lock.json','pyproject.toml']:
        if (ROOT/name).is_file():shutil.copy2(ROOT/name,out/name)
    ledger=read_json(ROOT/'experiments/seed-ledger.json')
    ids={r['run_id'] for r in ledger['runs']}
    for path in (ROOT/'experiments').glob('frozen-*/experiments/seed-ledger.json'):
        for run in read_json(path)['runs']:
            if run['run_id'] not in ids:ledger['runs'].append(run);ids.add(run['run_id'])
    write_json(ROOT/'experiments/seed-ledger.json',ledger)
    artifact=None
    if a.artifact_root is not None:
        if os.name!='nt':raise SystemExit('This artifact junction option currently requires Windows')
        artifact=a.artifact_root.resolve()/out.name
        if artifact.exists():raise SystemExit('Artifact destination exists; never overwrite it')
        artifact.mkdir(parents=True)
        link=out/'experiments'
        env=dict(os.environ,SPIRE_OUTPUT_LINK=str(link),SPIRE_OUTPUT_TARGET=str(artifact))
        subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',
            "$ErrorActionPreference='Stop'; New-Item -ItemType Junction -Path $env:SPIRE_OUTPUT_LINK -Value $env:SPIRE_OUTPUT_TARGET | Out-Null"],
            env=env,check=True,capture_output=True)
        if not os.path.samefile(link,artifact):raise SystemExit('Artifact junction verification failed')
    write_json(out/'experiments/seed-ledger.json',ledger)
    write_json(out/'freeze.json',{'source_version':version_hash(),'source':str(ROOT),'destination':str(out),
                                'artifact_root':str(artifact)if artifact else None})
    print(out,flush=True)

if __name__=='__main__':main()
