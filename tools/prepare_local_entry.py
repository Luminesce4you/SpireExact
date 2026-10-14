"""Prepare local ledger/storage and the frozen-i100r snapshot that the SpireBoard entry runs."""
from pathlib import Path
import argparse
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    if sys.version_info < (3,12):
        raise SystemExit('Python 3.12+ is required for this source candidate')
    parser = argparse.ArgumentParser()
    parser.add_argument('--artifact-root', type=Path, default=ROOT/'outputs/research')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    setup = ROOT/'.tools/source-setup.json'
    if not setup.is_file():
        raise SystemExit('Run tools/setup_source.py first; no source setup is present')
    data = ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
    if not (data/'sts2.dll').is_file():
        raise SystemExit('Private game dependency is missing')
    if args.check:
        print(json.dumps({'setup_present':True,'native_jobs_started':0,'snapshot_present':(ROOT/'experiments/frozen-i100r/freeze.json').is_file()}))
        return
    artifact = args.artifact_root.resolve()
    artifact.mkdir(parents=True,exist_ok=True)
    policy = ROOT/'storage-policy.json'
    if not policy.exists():
        policy.write_text(json.dumps({'schema':'spire-storage/v1','artifact_roots':[str(artifact)],
             'task_cap_decimal_gb':150,'admission_cap_decimal_gb':110,'per_job_write_cap_decimal_gb':8,
             'max_concurrent_seed_jobs':1,'recent_detailed_seed_window':40},indent=2),encoding='utf-8')
    ledger = ROOT/'experiments/seed-ledger.json'
    ledger.parent.mkdir(exist_ok=True)
    if not ledger.exists():
        ledger.write_text(json.dumps({'reserved':['0','1','2','42','43','100','101','102','103','104'],'runs':[]},indent=2),encoding='utf-8')
    freeze = ROOT/'experiments/frozen-i100r'
    if not freeze.exists():
        subprocess.run([sys.executable,str(ROOT/'tools/freeze_experiment.py'),'frozen-i100r',
                        '--artifact-root',str(artifact)],cwd=ROOT,check=True)
    print(json.dumps({'snapshot':str(freeze),
                       'next':'powershell -NoProfile -File dashboard/start.ps1 -PythonExe <python 3.12>'}))


if __name__=='__main__':
    main()
