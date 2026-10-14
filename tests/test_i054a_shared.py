"""frozen-i054a-shm: i101b's --worker-memory-policy shared, ported unchanged onto frozen-i054a (from frozen-i101b tests/test_i101_shared.py)."""
import contextlib, io, json, os, tempfile, unittest
from pathlib import Path
from unittest import mock

from spire_exact.planning import resources
from spire_exact.planning.pool import MEMORY_POLICIES, NativePool, NativeWorker, SharedMemoryBudget, WorkerError
from spire_exact.planning.resources import ResourcePlan, MIB

REASON = 'NATIVE_TASK_SHARED_MEMORY'


def memory(ws, commit):
    return lambda pid, field='WorkingSetSize': commit if field == 'PagefileUsage' else ws


class BudgetTests(unittest.TestCase):
    def test_latest_started_running_task_gives_way(self):
        budget, old, young = SharedMemoryBudget(300 * MIB, 2), object(), object()
        budget.enter(old, 1.0);budget.enter(young, 2.0)
        self.assertIsNone(budget.update(old, 200 * MIB))
        self.assertIsNone(budget.update(young, 50 * MIB))
        self.assertIsNone(budget.update(old, 260 * MIB))  # over budget, but old is not the latest started
        decision = budget.update(young, 60 * MIB)
        self.assertEqual(decision, {'budget_bytes': 300 * MIB, 'total_bytes': 320 * MIB, 'running_tasks': 2})
        self.assertEqual((budget.report['victims'], budget.report['peak_total_bytes']), (1, 320 * MIB))
        self.assertNotIn(id(young), budget.started)
        self.assertIsNone(budget.update(young, 60 * MIB))  # a stopped task is never chosen twice
        self.assertIsNone(budget.update(old, 280 * MIB))

    def test_idle_processes_count_but_are_never_victims(self):
        budget, idle, heavy, run = SharedMemoryBudget(300 * MIB, 2), object(), object(), object()
        budget.enter(idle, 1.0)
        self.assertFalse(budget.release(idle, 150 * MIB))  # within the fair share: stays alive
        self.assertTrue(budget.release(heavy, 151 * MIB))  # above it: recycle at the boundary
        self.assertEqual(budget.report['idle_recycles'], 1)
        budget.forget(heavy)
        budget.enter(run, 3.0)
        self.assertIsNone(budget.update(run, 150 * MIB))
        decision = budget.update(run, 151 * MIB)  # 150 idle + 151 > 300: the only running task stops
        self.assertEqual(decision['running_tasks'], 1)
        budget.leave(run);budget.forget(idle)
        self.assertNotIn(id(idle), budget.commit)

    def test_unregistered_worker_is_ignored(self):
        budget = SharedMemoryBudget(100 * MIB, 1)
        self.assertIsNone(budget.update(object(), 500 * MIB))
        self.assertEqual(budget.report['peak_total_bytes'], 0)

    def test_policy_is_offered(self):
        self.assertEqual(MEMORY_POLICIES, ('hard', 'recycle-at-boundary', 'shared'))

    @unittest.skipUnless(os.name == 'nt', 'commit charge is read on Windows')
    def test_commit_is_read_from_the_process(self):
        self.assertGreater(resources.process_memory(os.getpid(), 'PagefileUsage'), 0)
        self.assertGreater(resources.process_memory(os.getpid()), 0)


class WorkerTests(unittest.TestCase):
    def worker(self, budget):
        plan = ResourcePlan(2, 1, 300 * MIB, 0, 8, 16 * 1024 ** 3)
        worker = NativeWorker('dotnet', Path('host'), Path('game'), Path('workers'), plan,
                              memory_policy='shared', shared=budget)
        worker.process = mock.Mock(pid=101)
        worker.start = mock.Mock()
        worker.close = mock.Mock()
        worker.inbox.put('SPIRE_WORKER_RESULT ' + json.dumps({'healthy': True}))
        return worker

    def test_younger_task_is_stopped_with_its_measurements(self):
        budget = SharedMemoryBudget(300 * MIB, 2)
        older, younger = object(), self.worker(budget)
        budget.enter(older, -1.0);budget.update(older, 200 * MIB)
        with mock.patch('spire_exact.planning.pool.process_memory', side_effect=memory(50 * MIB, 150 * MIB)):
            with self.assertRaisesRegex(WorkerError, REASON):
                younger.execute(Path('request'))
        self.assertEqual(younger.failure_metrics['shared_memory']['total_bytes'], 350 * MIB)
        younger.close.assert_called_once()
        self.assertEqual(budget.report['victims'], 1)
        self.assertEqual(set(budget.started), {id(older)})

    def test_older_task_finishes_and_stays_idle(self):
        budget = SharedMemoryBudget(300 * MIB, 2)
        older, younger = self.worker(budget), object()
        budget.enter(younger, 1e18);budget.update(younger, 200 * MIB)
        with mock.patch('spire_exact.planning.pool.process_memory', side_effect=memory(50 * MIB, 150 * MIB)):
            result = older.execute(Path('request'))
        self.assertEqual(result['worker_job'], 1)
        self.assertFalse(older.recycle_pending)
        self.assertEqual(budget.commit[id(older)], 150 * MIB)
        self.assertNotIn(id(older), budget.started)
        self.assertIsNotNone(budget.update(younger, 200 * MIB))

    def test_heavy_idle_process_is_recycled_at_the_boundary(self):
        budget = SharedMemoryBudget(300 * MIB, 2)
        worker = self.worker(budget)
        with mock.patch('spire_exact.planning.pool.process_memory', side_effect=memory(50 * MIB, 200 * MIB)):
            worker.execute(Path('request'))
        self.assertTrue(worker.recycle_pending)
        self.assertEqual(budget.report['idle_recycles'], 1)

    def test_hard_policy_is_unchanged(self):
        plan = ResourcePlan(2, 1, 300 * MIB, 0, 8, 16 * 1024 ** 3)
        worker = NativeWorker('dotnet', Path('host'), Path('game'), Path('workers'), plan)
        self.assertIsNone(worker.shared)
        worker.process = mock.Mock(pid=101);worker.start = mock.Mock();worker.close = mock.Mock()
        with mock.patch('spire_exact.planning.pool.process_memory', return_value=301 * MIB):
            with self.assertRaisesRegex(WorkerError, 'NATIVE_TASK_MEMORY_BUDGET'):
                worker.execute(Path('request'))

    def test_pool_shares_one_budget(self):
        budget_bytes = 19456 * MIB
        plan = ResourcePlan(6, 1, budget_bytes, 1024 * MIB, 8, 20480 * MIB)
        inputs = {'dependencies': {}}
        with tempfile.TemporaryDirectory() as folder, \
             mock.patch('spire_exact.planning.pool.build_host', return_value=('dotnet', Path('host'), {'host_sha256': 'host'})), \
             mock.patch('spire_exact.planning.pool.input_fingerprint', return_value=inputs), \
             mock.patch('spire_exact.planning.pool.enclosing_job_memory_limit', return_value=24 * 1024 ** 3), \
             mock.patch('spire_exact.planning.pool.NativeWorker') as worker_class:
            pool = NativePool(Path('game'), Path(folder) / 'workers', plan, runtime_profile='server-large-gen0',
                              memory_policy='shared')
            try:
                self.assertEqual(worker_class.call_count, 6)
                self.assertTrue(all(call.kwargs['shared'] is pool.shared for call in worker_class.call_args_list))
                self.assertIs(pool.stats['shared_memory'], pool.shared.report)
                self.assertEqual(pool.stats['shared_memory']['budget_bytes'], budget_bytes)
                self.assertEqual(pool.stats['enforced_job_memory_limit_bytes'], 24 * 1024 ** 3)
                self.assertNotIn('gc_heap_hard_limit_bytes', pool.runtime)  # the focus profile's runtime has no heap cap
            finally:
                pool.close()


class PlanTests(unittest.TestCase):
    def detect(self, commit_mib, job_mib=20480):
        with mock.patch.object(resources, 'available_commit', return_value=commit_mib * MIB), \
             mock.patch.object(resources, 'effective_cpus', return_value=8.0), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('SPIRE_MEMORY_BUDGET_MIB', None)
            return ResourcePlan.detect(6, 1, None, 1024, job_mib * MIB, shared=True)

    def test_one_budget_for_all_workers(self):
        plan = self.detect(32000)
        self.assertEqual((plan.workers, plan.worker_memory_bytes), (6, (20480 - 1024) * MIB))
        plan = self.detect(15000)
        self.assertEqual((plan.workers, plan.worker_memory_bytes), (6, (15000 - 1024) * MIB))

    def test_low_physical_memory_does_not_refuse_shared(self):
        # The iteration-104 refusal: 7 x 2560 + 1024 MiB did not fit 16.4 GB of available RAM.
        with mock.patch.object(resources, 'available_memory', return_value=16418 * MIB), \
             mock.patch.object(resources, 'available_commit', return_value=31000 * MIB), \
             mock.patch.object(resources, 'effective_cpus', return_value=8.0):
            os.environ.pop('SPIRE_MEMORY_BUDGET_MIB', None)
            self.assertEqual(ResourcePlan.detect(7, 1, 2560, 1024).workers, 6)
            plan = ResourcePlan.detect(7, 1, None, 1024, 24576 * MIB, shared=True)
        self.assertEqual((plan.workers, plan.worker_memory_bytes), (7, (24576 - 1024) * MIB))

    def test_refusals(self):
        for args in ((6, 1, 900, 1024, 20480 * MIB), (6, 1, None, 1024, None), (None, 1, None, 1024, 20480 * MIB)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                ResourcePlan.detect(*args, shared=True)

    def test_cli_needs_explicit_workers(self):
        from spire_exact.planning.__main__ import main
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stderr(io.StringIO()) as err, \
             self.assertRaises(SystemExit):
            main(['--out', str(Path(folder) / 'unused'), '--seed', '7', '--worker-memory-policy', 'shared'])
        self.assertIn('needs an explicit --workers', err.getvalue())


if __name__ == '__main__':
    unittest.main()
