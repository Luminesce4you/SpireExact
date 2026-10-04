import json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from dashboard import job_control


class JobControlTests(unittest.TestCase):
    def test_legacy_terminal_run_has_no_process_capabilities(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'launch-state.json').write_text('{"phase":"stopped"}')
            value = job_control.status(root)
            self.assertEqual(value['phase'], 'stopped')
            self.assertFalse(any(value[key] for key in ('can_pause', 'can_resume', 'can_stop')))

    def test_pause_ledger_subtracts_open_and_completed_intervals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(job_control.time, 'perf_counter', return_value=10):
                first = job_control._ledger(root, 'pausing', begin=True)
            self.assertEqual(first['paused_started_monotonic'], 10)
            with patch.object(job_control.time, 'perf_counter', return_value=25):
                second = job_control._ledger(root, 'running', finish=True)
            self.assertEqual(second['paused_total_seconds'], 15)
            self.assertIsNone(second['paused_started_monotonic'])
            with patch.object(job_control.time, 'perf_counter', return_value=50):
                job_control._ledger(root, 'paused', begin=True)
            with patch.object(job_control.time, 'perf_counter', return_value=70):
                done = job_control._ledger(root, 'cancelled', finish=True)
            self.assertEqual(done['paused_total_seconds'], 35)

    def test_legacy_cancel_retains_last_saved_search_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'launch-state.json').write_text('{"phase":"stopped"}')
            (root / 'manual-request.json').write_text('{"seed":"42"}')
            (root / 'seed-42').mkdir()
            (root / 'seed-42/result.json').write_text('{"elapsed_seconds":654.75}')
            value = job_control.status(root)
            self.assertEqual(value['active_elapsed_seconds'], 654.75)
            self.assertFalse(value['can_resume'])

    def test_status_accounts_for_pause_before_solve_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'control.json').write_text(json.dumps({'schema': job_control.SCHEMA,
                'phase': 'paused', 'started_counter': 100, 'pause_offset_at_start': 30,
                'suspended': [{"pid": 7, "created_ticks": 9}]}))
            (root / 'pause-ledger.json').write_text(json.dumps({'paused_total_seconds': 50,
                'paused_started_monotonic': 150}))
            with patch.object(job_control.time, 'perf_counter', return_value=180):
                value = job_control.status(root)
            self.assertEqual(value['active_elapsed_seconds'], 30)
            # No live registered named Job: never advertise memory recovery.
            self.assertEqual(value['phase'], 'stopped')
            self.assertFalse(value['can_resume'])

    def test_finished_status_uses_the_end_clock_not_time_after_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'control.json').write_text(json.dumps({'schema': job_control.SCHEMA,
                'phase': 'completed', 'started_counter': 100, 'finished_counter': 160}))
            (root / 'pause-ledger.json').write_text('{"paused_total_seconds":15}')
            with patch.object(job_control.time, 'perf_counter', return_value=999):
                value = job_control.status(root)
            self.assertEqual(value['active_elapsed_seconds'], 45)
            self.assertEqual(value['phase'], 'completed')
            self.assertFalse(value['can_stop'])


if __name__ == '__main__': unittest.main()
