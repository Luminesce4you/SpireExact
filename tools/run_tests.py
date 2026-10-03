"""Run actual stdlib tests and retain evidence. Native runtime tests are not included."""
from pathlib import Path
import os
import sys
import unittest
import platform
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.chdir(ROOT)
from spire_exact.canonical import write_json
class EvidenceResult(unittest.TextTestResult):
    def __init__(self,*args,**kwargs): super().__init__(*args,**kwargs);self.passed=[]
    def addSuccess(self,test):super().addSuccess(test);self.passed.append(test.id())
suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'))
output=ROOT/'outputs';output.mkdir(exist_ok=True)
with (output/'tests.log').open('w',encoding='utf-8') as log:
    result=unittest.TextTestRunner(stream=log,verbosity=2,resultclass=EvidenceResult).run(suite)
report={'python':platform.python_version(),'platform':platform.system(),
        'tests_run':result.testsRun,'passed':len(result.passed),'failures':len(result.failures),
        'errors':len(result.errors),'skipped':len(result.skipped),
        'successful':result.wasSuccessful(),'native_runtime_tests_run':0,
        'native_probe_compiled':False,'passed_tests':result.passed,
        'failure_details':[{'test':str(test),'traceback':trace} for test,trace in result.failures+result.errors]}
write_json(output/'tests.json',report)
print({key:value for key,value in report.items() if key not in ('passed_tests','failure_details')})
if not result.wasSuccessful():
    print((output/'tests.log').read_text(encoding='utf-8'))
raise SystemExit(0 if result.wasSuccessful() else 1)
