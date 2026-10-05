"""Run Python/source tests and save actual evidence. Never invokes native setup."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import shutil
import sys
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tests'))
from tools.benchmark_i085 import source_hash

BASELINE_ISSUES={
    'test_dashboard_controls.DashboardControlTests.test_180_minutes_remains_valid_and_entry_records_latest_options':
        'Reproduced in unmodified source CI: test expects 180 minutes, public dashboard accepts 1..45.',
    'setUpClass (test_solver_patches.SolverPatchContractTests)':
        'Reproduced in unmodified source CI: vendor/CombatSolver has not been installed in source-only environment.'}


class EvidenceResult(unittest.TextTestResult):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.successes=[]
    def addSuccess(self,test):
        super().addSuccess(test);self.successes.append(test.id())


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--candidate-only',action='store_true')
    a=p.parse_args(argv);out=a.out.resolve()
    if out.exists():p.error('use a new evidence directory')
    out.mkdir(parents=True)
    before=source_hash();start=time.perf_counter()
    suite=unittest.TestLoader().discover(str(ROOT/'tests'),pattern='test_i085*.py' if a.candidate_only else 'test*.py')
    with (out/'unittest.log').open('x',encoding='utf-8') as log:
        result=unittest.TextTestRunner(stream=log,verbosity=2,resultclass=EvidenceResult).run(suite)
    failures=[{'test':test.id(),'traceback':text,'baseline_issue':BASELINE_ISSUES.get(test.id())}
              for test,text in result.failures+result.errors]
    record={'schema':'spire-i085-python-validation/v1','timestamp_utc':datetime.now(timezone.utc).isoformat(),
        'platform':platform.platform(),'python':sys.version,'source_hash':before,'source_unchanged':before==source_hash(),
        'candidate_only':a.candidate_only,'tests_run':result.testsRun,'passed':len(result.successes),
        'failures':len(result.failures),'errors':len(result.errors),'skips':len(result.skipped),
        'duration_seconds':time.perf_counter()-start,'successful':result.wasSuccessful(),
        'passed_tests':result.successes,'issues':failures,
        'skipped_tests':[{'test':test.id(),'reason':reason} for test,reason in result.skipped],
        'unexpected_successes':[test.id() for test in result.unexpectedSuccesses],
        'expected_failures':[test.id() for test,_ in result.expectedFailures],
        'environment':{'dotnet_on_path':shutil.which('dotnet'),'windows':sys.platform=='win32',
                       'vendor_source_present':(ROOT/'vendor/CombatSolver/src').is_dir()},
        'native_build_executed':False,'native_rollouts_executed':False,'verified_native_wins':0,
        'scope':'Python tests including mocked hosts and fabricated contract fixtures; not native efficacy evidence'}
    (out/'tests.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:record[k] for k in ('tests_run','passed','failures','errors','skips','successful','source_unchanged')}))
    return 0 if record['successful'] and record['source_unchanged'] else 1


if __name__=='__main__':raise SystemExit(main())
