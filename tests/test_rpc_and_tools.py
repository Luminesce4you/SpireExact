import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from spire_exact.canonical import ContractError, UnsupportedSemantic, read_json
from spire_exact.core import solve
from spire_exact.rpc import RpcModel
from spire_exact.symbolic import GraphModel
from spire_exact.upstream import doctor, PIN
from spire_exact.verify import verify_graph

ROOT=Path(__file__).resolve().parents[1]
class RpcTests(unittest.TestCase):
    def command(self, *extras):
        return [sys.executable,'-m','spire_exact','rpc-server','--model',str(ROOT/'examples/delayed_reward.json'),*extras]
    def test_real_subprocess_roundtrip(self):
        with RpcModel(self.command(),cwd=str(ROOT)) as m:
            r=solve(m)
            self.assertEqual(r.result['best_value'],[1,10])
            self.assertTrue(verify_graph(m,r.graph)['verified'])
    def test_remote_incomplete_is_not_exact(self):
        with RpcModel(self.command('--max-successors','1'),cwd=str(ROOT)) as m:
            r=solve(m)
            self.assertEqual(r.result['status'],'INCOMPLETE')
            self.assertFalse(r.result['proven_optimal_in_model'])
    def test_remote_matches_inprocess(self):
        spec=read_json(ROOT/'examples/delayed_reward.json')
        local=solve(GraphModel(spec))
        with RpcModel(self.command(),cwd=str(ROOT)) as m: remote=solve(m)
        self.assertEqual(local.graph,remote.graph)
    def test_stdout_noise_is_rejected(self):
        with self.assertRaises((ContractError,ValueError)):
            RpcModel([sys.executable,'-c','print("not json")'])
    def test_missing_capability_is_rejected(self):
        script='import sys,json; r=json.loads(sys.stdin.readline()); print(json.dumps({"id":r["id"],"result":{"protocol":"spire-transition-jsonl/v1","capabilities":{}}}),flush=True)'
        with self.assertRaises(ContractError): RpcModel([sys.executable,'-c',script])
    def test_timeout_not_optimal(self):
        with self.assertRaises(UnsupportedSemantic):
            RpcModel([sys.executable,'-c','import time;time.sleep(5)'],timeout_seconds=1)
    def test_shutdown_kills_child(self):
        m=RpcModel(self.command(),cwd=str(ROOT));p=m.process;m.close();self.assertIsNotNone(p.poll())
    def test_invalid_command_rejected(self):
        with self.assertRaises(ContractError):RpcModel([])

class ToolingTests(unittest.TestCase):
    def test_diagnostics_do_not_claim_native_success(self):
        result=doctor()
        self.assertFalse(result['native_exact_adapter_implemented'])
        self.assertFalse(result['native_build_verified'])
        self.assertFalse(result['network_used'])
    def test_probe_patch_exact_anchor(self):
        spec=importlib.util.spec_from_file_location('probe_tool',ROOT/'tools/install_native_probe.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        source='before\n'+module.ANCHOR+'\nafter\n'
        output=module.apply_patch(source)
        self.assertIn('SPIREEXACT_ROOT_PROBE',output)
        with self.assertRaises(ContractError):module.apply_patch('not the harness')
        with self.assertRaises(ContractError):module.apply_patch(source+source)
    def test_pin_is_immutable_full_sha(self):
        self.assertEqual(len(PIN),40)
        self.assertEqual(read_json(ROOT/'upstream.lock.json')['commit'],PIN)
    def test_seed_command_refuses_fake_solution(self):
        p=subprocess.run([sys.executable,'-m','spire_exact','solve-seed','42','--attempts','0'],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(p.returncode,1)
        payload=json.loads(p.stderr)
        self.assertEqual(payload['status'],'ERROR')
        self.assertFalse(payload['proven_optimal'])
    def test_cli_graph_output_can_be_checked(self):
        with tempfile.TemporaryDirectory() as d:
            p=subprocess.run([sys.executable,'-m','spire_exact','solve','examples/delayed_reward.json','--out',d],cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertTrue(read_json(Path(d)/'verification.json')['verified'])
    def test_native_metric_floats_are_kept_as_telemetry_not_exact_state(self):
        # The native wrapper reads ordinary metrics JSON; exact states reject floats separately.
        from spire_exact.upstream import native_baseline
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);source=base/'upstream';out=base/'out'
            harness=source/'tools/OfflineSearchHarness/bin/Release/net9.0/OfflineSearchHarness.dll'
            harness.parent.mkdir(parents=True);harness.write_bytes(b'test-not-an-assembly')
            def fake_run(command,*args,**kwargs):
                out.mkdir(exist_ok=True)
                (out/'harness-result.json').write_text('{"wallSeconds":1.25}',encoding='utf-8')
                return 'mock execution, not real native validation'
            with patch('spire_exact.upstream.verify_upstream',return_value={'commit':PIN}),patch('spire_exact.upstream.run',side_effect=fake_run):
                result=native_baseline(source,out)
            self.assertFalse(result['proven_optimal'])
            self.assertEqual(result['harness_result']['wallSeconds'],1.25)

class OutputSafetyTests(unittest.TestCase):
    def test_nonempty_directory_cannot_leave_stale_optimality_file(self):
        with tempfile.TemporaryDirectory() as d:
            marker=Path(d)/'verification.json';marker.write_text('{"old":true}',encoding='utf-8')
            process=subprocess.run([sys.executable,'-m','spire_exact','solve','examples/delayed_reward.json',
                                    '--max-states','1','--out',d],cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(process.returncode,1)
            self.assertIn('not empty',process.stderr)
            self.assertFalse((Path(d)/'result.json').exists())
