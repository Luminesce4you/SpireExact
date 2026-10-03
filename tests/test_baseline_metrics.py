import json
from pathlib import Path
import tempfile
import unittest

from tools.baseline_metrics import read_ledger, summarize_runtime
from tools.audit_a10_baseline import audit


class BaselineMetricsTests(unittest.TestCase):
    def test_cache_hit_does_not_double_charge_native_work(self):
        row = {'kind': 'deck', 'performance': {'wall_us': 2000000, 'cpu_us': 1000000, 'cycles': 123456,
               'allocated_bytes': 4000, 'exclusive_stages': {'beam_search': {'us': 1500000}}},
               'expanded_combat_nodes': 20, 'advisor_metrics': {'searches': [
                   {'wall_us': 1500000, 'gc_pause_ms': 150}]}}
        report = summarize_runtime([row, dict(row, cache_hit=True)], {'wall_seconds': 4, 'job_cpu_seconds': 3})
        self.assertEqual(report['combat_nodes'], 20)
        self.assertEqual(report['native_service_seconds_sum'], 2)
        self.assertEqual(report['integrated_native_service_concurrency'], .5)
        self.assertEqual(report['job_busy_cores'], .75)
        self.assertEqual(report['gc_pause_fraction_of_search_wall'], .1)
        self.assertEqual(report['allocated_bytes_per_node'], 200)
        self.assertEqual(report['native_process_cycles_sum'],123456)
        self.assertEqual(report['cycle_profiled_evaluations'],1)
        self.assertFalse(report['cpu_accounting_is_utilization'])

    def test_missing_resource_observations_are_unknown(self):
        report = summarize_runtime([{'classification': 'NATIVE_CRASH'}], {})
        self.assertIsNone(report['job_busy_cores'])
        self.assertIsNone(report['native_cpu_per_service_second'])
        self.assertEqual(report['profiled_evaluations'], 0)
        self.assertIsNone(report['native_process_cycles_sum'])

    def test_ledger_preserves_valid_rows_and_reports_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'ledger'
            path.write_bytes(b'{"label":"a"}\ninvalid\n{"label":"b"}\n{"label":')
            rows, issues = read_ledger(path)
            self.assertEqual([r['label'] for r in rows], ['a', 'b'])
            self.assertEqual([i['line'] for i in issues], [2, 4])
            self.assertTrue(issues[0]['newline_terminated'])
            self.assertFalse(issues[1]['newline_terminated'])

    def test_censored_run_still_generates_performance_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run = root / 'run'; out = root / 'audit'
            seed = run / 'seed-10'; seed.mkdir(parents=True)
            (run / 'validation-manifest.json').write_text(json.dumps({'seed': '10', 'old_prefixes_loaded': False}))
            (run / 'baseline-report.json').write_text('{"exit_code":124}')
            row = {'label': 'eval-0000', 'classification': 'NATIVE_ROUTE_DEATH', 'observation': {'floor': 17}}
            (seed / 'evaluations.jsonl').write_text(json.dumps(row) + '\n{"label":')
            (seed / 'result.json').write_text('{"status":"UNKNOWN","evaluations":[]}')
            audit(run, out)
            report = json.loads((out / 'audit.json').read_text())
            self.assertFalse(report['evidence_valid'])
            self.assertTrue(report['censored'])
            self.assertEqual(report['evaluations'], 1)
            self.assertEqual(report['exit_code'], 124)
            self.assertIsNone(report['busy_cores'])
            self.assertIsNone(report['progress_curve'][0]['wall_seconds'])
            self.assertEqual(len(report['ledger_issues']), 1)

    def test_native_death_can_have_complete_evidence_without_being_a_win(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); seed = root / 'run/seed-1'; seed.mkdir(parents=True)
            row = {'label': 'eval-0000', 'classification': 'NATIVE_ROUTE_DEATH', 'observation': {'floor': 17}}
            files = {
                'validation-manifest.json': {'seed': '1', 'old_prefixes_loaded': False},
                'baseline-report.json': {'exit_code': 0},
                'seed-1-resources.json': {'wall_seconds': 10, 'job_cpu_seconds': 20, 'job_write_transfer_bytes': 100},
                'seed-1/result.json': {'status': 'UNKNOWN', 'evaluations': [row]},
            }
            for name, value in files.items():
                (root / 'run' / name).write_text(json.dumps(value))
            (seed / 'evaluations.jsonl').write_text(json.dumps(row) + '\n')
            audit(root / 'run', root / 'audit')
            report = json.loads((root / 'audit/audit.json').read_text())
            self.assertTrue(report['evidence_valid'])
            self.assertTrue(report['censored'])
            self.assertIsNone(report['witness'])
            self.assertEqual(report['busy_cores'], 2)
            self.assertIsNone(report['m0_cpu_gate'])
            self.assertIsNone(report['m0_worker_occupancy_gate'])


if __name__ == '__main__':
    unittest.main()
