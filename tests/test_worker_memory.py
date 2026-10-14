import json
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from spire_exact.planning.pool import NativeWorker, NativePool, WorkerError, memory_settings
from spire_exact.planning.resources import ResourcePlan, MIB


class WorkerMemoryTests(unittest.TestCase):
    def worker(self, policy='hard'):
        plan = ResourcePlan(1, 1, 128*MIB, 0, 8, 14*1024**3)
        with patch('spire_exact.planning.pool.enclosing_job_memory_limit', return_value=14*1024**3):
            worker = NativeWorker('dotnet', Path('host'), Path('game'), Path('workers'), plan,
                                  memory_policy=policy)
        worker.process = Mock(pid=101)
        worker.start = Mock()
        worker.close = Mock()
        worker.inbox.put('SPIRE_WORKER_RESULT ' + json.dumps({'healthy': True}))
        return worker

    def test_recycling_requires_real_job_cap_and_fails_closed(self):
        for cap in (None, 0, 15*1024**3):
            with self.subTest(cap=cap), patch('spire_exact.planning.pool.enclosing_job_memory_limit', return_value=cap):
                with self.assertRaisesRegex(ValueError, 'enforced Windows Job'):
                    memory_settings('recycle-at-boundary')
        with patch('spire_exact.planning.pool.enclosing_job_memory_limit', return_value=6*1024**3):
            self.assertEqual(memory_settings('recycle-at-boundary')['enforced_job_memory_limit_bytes'], 6*1024**3)

    def test_default_still_aborts_over_limit_task(self):
        worker = self.worker()
        with patch('spire_exact.planning.pool.process_memory', return_value=129*MIB):
            with self.assertRaisesRegex(WorkerError, 'NATIVE_TASK_MEMORY_BUDGET'):
                worker.execute(Path('request'))
        worker.close.assert_called_once()
        self.assertEqual(worker.jobs, 0)

    def test_boundary_policy_finishes_before_recycle_and_keeps_task_peak_separate(self):
        worker = self.worker('recycle-at-boundary')
        worker.peak_rss = 200*MIB
        with patch('spire_exact.planning.pool.process_memory', side_effect=[129*MIB, 80*MIB]):
            result = worker.execute(Path('request'))
        self.assertTrue(worker.recycle_pending)
        self.assertEqual(result['recycle_reason'], 'rss_threshold')
        self.assertEqual(result['peak_sampled_rss'], 129*MIB)
        self.assertEqual(result['process_peak_sampled_rss'], 200*MIB)
        self.assertEqual(result['worker_job'], 1)
        worker.close.assert_not_called()  # pool first persists the completed result

    def test_short_task_completion_sample_triggers_recycle(self):
        worker = self.worker('recycle-at-boundary')
        with patch('spire_exact.planning.pool.process_memory', side_effect=[80*MIB, 150*MIB]):
            result = worker.execute(Path('request'))
        self.assertEqual(result['peak_sampled_rss'], 150*MIB)
        self.assertTrue(worker.recycle_pending)

    def test_boundary_mode_does_not_ignore_timeout_or_native_failure(self):
        worker = self.worker('recycle-at-boundary')
        with self.assertRaisesRegex(WorkerError, 'NATIVE_TASK_TIMEOUT'):
            worker.execute(Path('request'), timeout=-1)
        worker.close.assert_called_once()
        worker = self.worker('recycle-at-boundary')
        worker.inbox = queue.Queue()
        worker.inbox.put('SPIRE_WORKER_RESULT {"healthy": false, "error": "native failure"}')
        with patch('spire_exact.planning.pool.process_memory', return_value=150*MIB):
            with self.assertRaisesRegex(WorkerError, 'NATIVE_WORKER_FAILED'):
                worker.execute(Path('request'))
        worker.close.assert_called_once()

    def test_pool_persists_healthy_result_before_recycling_and_releases_worker(self):
        worker = self.worker('recycle-at-boundary')
        worker.recycle_pending = True
        worker.jobs = 1
        worker.execute = Mock(return_value={'recycle_reason': 'rss_threshold'})
        identity = {'host_sha256': 'host', 'game_sha256': 'game', 'godot_sha256': 'godot', 'harmony_sha256': 'harmony'}
        inputs = {'dependencies': {'sts2.dll': 'game', 'GodotSharp.dll': 'godot', '0Harmony.dll': 'harmony'}}
        with tempfile.TemporaryDirectory() as folder, \
             patch('spire_exact.planning.pool.build_host', return_value=('dotnet', Path('host'), {'host_sha256': 'host'})), \
             patch('spire_exact.planning.pool.input_fingerprint', return_value=inputs), \
             patch('spire_exact.planning.pool.NativeWorker', return_value=worker), \
             patch('spire_exact.planning.pool.available_memory', return_value=20*1024**3), \
             patch('spire_exact.planning.pool.read_json', side_effect=[{'trace': [1]}, identity]):
            target = Path(folder)/'result'
            worker.close.side_effect = lambda: self.assertTrue((target/'transport.json').is_file())
            pool = NativePool(Path('game'), Path(folder)/'workers', worker.plan)
            try:
                result, _ = pool.run({'history': []}, target)
                self.assertEqual(result, {'trace': [1]})
                worker.close.assert_called_once()
                self.assertEqual(pool.stats['memory_recycles'], 1)
                self.assertFalse(worker.recycle_pending)
                self.assertEqual(pool.available.qsize(), 1)
            finally:
                worker.close.side_effect = None
                pool.close()

