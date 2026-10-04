import json
import hashlib
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from spire_exact.planning.pool import NativeWorker, NativePool, WorkerError, memory_settings
from spire_exact.planning.resources import ResourcePlan, MIB
from spire_exact.planning.archive import classify_failure


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


class WorkerMemoryTelemetryTests(unittest.TestCase):
    """Real files and fake stdin/process/RSS only; no native process or game."""
    worker = WorkerMemoryTests.worker

    def request(self, root, **flags):
        output = root/'data'; output.mkdir()
        request = {'out':str(output.resolve()), 'history':[{'kind':'map','col':0,'row':1}],
                   'memory_telemetry':True, **flags}
        path = root/'request.json'
        path.write_text(json.dumps(request,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        return path, request, output/'memory-latest.json'

    def heartbeat(self, request_path, **overrides):
        return {'schema':'spire-worker-memory/v1','pid':101,
                'request_sha256':hashlib.sha256(request_path.read_bytes()).hexdigest(),
                'sequence':1,'kind':'heartbeat','task_elapsed_seconds':1.25,
                'gc_heap_size_bytes':64*MIB,'gc_total_committed_bytes':80*MIB,
                'gc_fragmented_bytes':2*MIB,'gc_pause_time_percentage':1.5,
                'gc_collection_counts':[1,0,0],'gc_collection_deltas':[0,0,0],
                'private_bytes':100*MIB,'rss_bytes':85*MIB,
                'progress':{'completed_actions':5,'completed_prefix_length':5,'requested_prefix_length':1,
                            'restored_prefix':0,'phase':'combat','act':2,'floor':43,
                            'combat_active':True,'safe_journal_prefix_length':4}, **overrides}

    def abort(self, worker, path, request, samples):
        with patch('spire_exact.planning.pool.process_memory',side_effect=samples), \
             patch('spire_exact.planning.pool.queue_get',side_effect=queue.Empty):
            with self.assertRaisesRegex(WorkerError,'^NATIVE_TASK_MEMORY_BUDGET$'):
                worker.execute(path,task_request=request)
        return worker.failure_metrics

    def assert_task_measurement(self, metrics):
        self.assertIsInstance(metrics,dict)
        for name in ('worker_task_active_seconds','worker_task_wall_seconds'):
            self.assertIsInstance(metrics[name],(int,float));self.assertGreaterEqual(metrics[name],0)
        self.assertTrue(metrics['recent_rss_samples'])
        for sample in metrics['recent_rss_samples']:
            self.assertGreaterEqual(sample['active_seconds'],0);self.assertGreaterEqual(sample['wall_seconds'],0)
            self.assertIs(type(sample['rss_bytes']),int)

    def test_native_oom_preserves_resource_kind_and_closes_under_both_memory_policies(self):
        for policy in ('hard','recycle-at-boundary'):
            with self.subTest(policy=policy):
                worker=self.worker(policy);worker.inbox=queue.Queue()
                worker.inbox.put('SPIRE_WORKER_RESULT '+json.dumps({
                    'healthy':False,'error_kind':'NATIVE_TASK_OUT_OF_MEMORY','error':'fake CLR allocation failure'}))
                with patch('spire_exact.planning.pool.process_memory',return_value=64*MIB):
                    with self.assertRaisesRegex(WorkerError,'^NATIVE_TASK_OUT_OF_MEMORY$'):
                        worker.execute(Path('request'))
                worker.close.assert_called_once()
                self.assertEqual(classify_failure({'status':'UNKNOWN','reason':'NATIVE_TASK_OUT_OF_MEMORY'}),'RESOURCE_LIMIT')
                self.assert_task_measurement(worker.failure_metrics)
                self.assertEqual(worker.failure_metrics['worker_job'],1)

    def test_oom_text_without_explicit_error_kind_does_not_reclassify_other_native_failure(self):
        worker=self.worker();worker.inbox=queue.Queue()
        worker.inbox.put('SPIRE_WORKER_RESULT '+json.dumps({'healthy':False,'error':'OutOfMemoryException mentioned by a fake diagnostic'}))
        with patch('spire_exact.planning.pool.process_memory',return_value=64*MIB):
            with self.assertRaisesRegex(WorkerError,'^NATIVE_WORKER_FAILED'):
                worker.execute(Path('request'))
        worker.close.assert_called_once()

    def test_rss_abort_keeps_last_bound_native_heartbeat_and_sampled_task_cost(self):
        with tempfile.TemporaryDirectory()as folder:
            path,request,beat_path=self.request(Path(folder));worker=self.worker()
            beat=self.heartbeat(path)
            # The producer publishes only after stdin dispatch, as a real worker does.
            worker.process.stdin.write.side_effect=lambda _:beat_path.write_text(json.dumps(beat),encoding='utf-8')
            metrics=self.abort(worker,path,request,[80*MIB,90*MIB,129*MIB])
            self.assert_task_measurement(metrics)
            self.assertEqual([row['rss_bytes']for row in metrics['recent_rss_samples']],[80*MIB,90*MIB,129*MIB])
            self.assertEqual(metrics['last_memory_heartbeat'],beat)
            self.assertIsNone(metrics['last_heartbeat_issue'])
            self.assertEqual(metrics['last_memory_heartbeat']['progress']['completed_actions'],5)
            worker.close.assert_called_once()

    def test_recent_rss_ring_is_bounded_without_losing_the_abort_sample(self):
        with tempfile.TemporaryDirectory()as folder:
            path,request,_=self.request(Path(folder));worker=self.worker()
            metrics=self.abort(worker,path,request,[64*MIB]*36+[129*MIB])
            self.assertEqual(len(metrics['recent_rss_samples']),32)
            self.assertEqual(metrics['recent_rss_samples'][-1]['rss_bytes'],129*MIB)
            self.assertIsNone(metrics['last_memory_heartbeat'])
            self.assertIsInstance(metrics['last_heartbeat_issue'],str)

    def test_missing_and_corrupt_heartbeat_are_unknown_and_do_not_stop_rss_sampling(self):
        for body in (None,'{','null'):
            with self.subTest(body=body),tempfile.TemporaryDirectory()as folder:
                path,request,beat_path=self.request(Path(folder));worker=self.worker()
                if body is not None:
                    worker.process.stdin.write.side_effect=lambda _,body=body:beat_path.write_text(body,encoding='utf-8')
                metrics=self.abort(worker,path,request,[80*MIB,129*MIB])
                self.assertIsNone(metrics['last_memory_heartbeat']);self.assertTrue(metrics['last_heartbeat_issue'])
                self.assertEqual(len(metrics['recent_rss_samples']),2)

    def test_wrong_request_pid_schema_and_nonfinite_or_bool_stats_are_not_trusted(self):
        mutations=[{'pid':102},{'request_sha256':'0'*64},{'schema':'spire-worker-memory/v2'},
                   {'kind':'periodic'},{'sequence':True},{'task_elapsed_seconds':float('nan')},
                   {'gc_pause_time_percentage':float('inf')},{'gc_collection_counts':[True,0,0]}]
        for mutation in mutations:
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory()as folder:
                path,request,beat_path=self.request(Path(folder));worker=self.worker();beat=self.heartbeat(path,**mutation)
                worker.process.stdin.write.side_effect=lambda _,beat=beat:beat_path.write_text(json.dumps(beat),encoding='utf-8')
                metrics=self.abort(worker,path,request,[129*MIB])
                self.assertIsNone(metrics['last_memory_heartbeat']);self.assertTrue(metrics['last_heartbeat_issue'])
                self.assertEqual(metrics['recent_rss_samples'][-1]['rss_bytes'],129*MIB)

    def test_heartbeat_from_another_request_cannot_replace_current_task_unknown(self):
        with tempfile.TemporaryDirectory()as folder:
            root=Path(folder);path,request,beat_path=self.request(root);worker=self.worker()
            stale=self.heartbeat(path)
            # Same PID and output path, but request bytes changed; a PID alone is insufficient.
            request['history'].append({'kind':'map','col':1,'row':2})
            path.write_text(json.dumps(request,indent=2)+'\n',encoding='utf-8')
            worker.failure_metrics={'last_memory_heartbeat':stale}
            worker.process.stdin.write.side_effect=lambda _:beat_path.write_text(json.dumps(stale),encoding='utf-8')
            metrics=self.abort(worker,path,request,[129*MIB])
            self.assertIsNone(metrics['last_memory_heartbeat']);self.assertTrue(metrics['last_heartbeat_issue'])

    def test_oversized_heartbeat_is_not_parsed_before_memory_budget_abort(self):
        with tempfile.TemporaryDirectory()as folder:
            path,request,beat_path=self.request(Path(folder));worker=self.worker()
            worker.process.stdin.write.side_effect=lambda _:beat_path.write_text('x'*(256*1024+1),encoding='utf-8')
            with patch('spire_exact.planning.pool.read_json',side_effect=AssertionError('oversized heartbeat must not be parsed')):
                metrics=self.abort(worker,path,request,[129*MIB])
            self.assertIsNone(metrics['last_memory_heartbeat']);self.assertTrue(metrics['last_heartbeat_issue'])

    def test_off_request_does_not_read_a_heartbeat_file(self):
        for request_kind in ('none','off'):
            with self.subTest(request_kind=request_kind),tempfile.TemporaryDirectory()as folder:
                path,request,beat_path=self.request(Path(folder),memory_telemetry=False);worker=self.worker()
                beat_path.write_text('{"this":"must not be read"}',encoding='utf-8')
                def read_guard(candidate,*args,**kwargs):
                    if Path(candidate).name=='memory-latest.json':raise AssertionError('off request read heartbeat')
                    return json.loads(Path(candidate).read_text(encoding='utf-8'))
                with patch('spire_exact.planning.pool.process_memory',return_value=64*MIB), \
                     patch('spire_exact.planning.pool.read_json',side_effect=read_guard):
                    transport=worker.execute(path,task_request=request if request_kind=='off'else None)
                self.assertEqual(transport['worker_job'],1);worker.close.assert_not_called()

    def test_prefix_preservation_flag_alone_enables_bound_heartbeat(self):
        with tempfile.TemporaryDirectory()as folder:
            path,request,beat_path=self.request(Path(folder),memory_telemetry=False,preserve_completed_prefix=True)
            worker=self.worker();beat=self.heartbeat(path)
            worker.process.stdin.write.side_effect=lambda _:beat_path.write_text(json.dumps(beat),encoding='utf-8')
            metrics=self.abort(worker,path,request,[129*MIB])
            self.assertEqual(metrics['last_memory_heartbeat'],beat)

    def test_oom_is_requeued_as_unknown_without_halving_original_tactical_nodes(self):
        # This is the existing toy coordinator, not a native campaign or seed run.
        from test_worker_time import LossyEvaluator,run
        LossyEvaluator.log=[];LossyEvaluator.plan={('macro_focus',0):['NATIVE_TASK_OUT_OF_MEMORY']}
        with patch('spire_exact.planning.search.REQUEUE_HOLD_SECONDS',0):
            result,_=run(evaluator=LossyEvaluator,requeue_lost=1)
        first=[request for kind,count,request in LossyEvaluator.log if kind=='macro_focus'and count==0]
        again=[request for _,count,request in LossyEvaluator.log if count==1]
        self.assertEqual(result['requeued_evaluations'],{'NATIVE_TASK_OUT_OF_MEMORY':1})
        self.assertEqual(len(again),1)
        self.assertEqual(again[0]['advisor'],first[0]['advisor'])
        self.assertEqual(again[0]['history'],first[0]['history'])

    def test_pool_persists_oom_heartbeat_as_unknown_and_returns_the_worker_slot(self):
        worker=self.worker();worker.inbox=queue.Queue()
        worker.inbox.put('SPIRE_WORKER_RESULT '+json.dumps({'healthy':False,
            'error_kind':'NATIVE_TASK_OUT_OF_MEMORY','error':'fake OOM'}))
        inputs={'dependencies':{'sts2.dll':'game','GodotSharp.dll':'godot','0Harmony.dll':'harmony'}}
        with tempfile.TemporaryDirectory()as folder, \
             patch('spire_exact.planning.pool.build_host',return_value=('dotnet',Path('host'),{'host_sha256':'host'})), \
             patch('spire_exact.planning.pool.input_fingerprint',return_value=inputs), \
             patch('spire_exact.planning.pool.NativeWorker',return_value=worker), \
             patch('spire_exact.planning.pool.available_memory',return_value=20*1024**3), \
             patch('spire_exact.planning.pool.process_memory',return_value=64*MIB):
            target=Path(folder)/'result'
            def publish(line):
                beat=self.heartbeat(Path(line.strip()),kind='oom')
                (target/'data').mkdir(parents=True,exist_ok=True)
                (target/'data'/'memory-latest.json').write_text(json.dumps(beat),encoding='utf-8')
            worker.process.stdin.write.side_effect=publish
            pool=NativePool(Path('game'),Path(folder)/'workers',worker.plan)
            try:
                with self.assertRaisesRegex(WorkerError,'^NATIVE_TASK_OUT_OF_MEMORY$'):
                    pool.run({'history':[{'kind':'map','col':0,'row':1}],'memory_telemetry':True},target)
                failure=json.loads((target/'failure.json').read_text(encoding='utf-8'))
                self.assertEqual(failure['status'],'UNKNOWN');self.assertEqual(failure['error'],'NATIVE_TASK_OUT_OF_MEMORY')
                self.assertEqual(failure['memory_failure']['last_memory_heartbeat']['kind'],'oom')
                self.assert_task_measurement(failure['memory_failure'])
                self.assertEqual(pool.available.qsize(),1);self.assertEqual(pool.stats['failed'],1)
                self.assertFalse((target/'transport.json').exists())
                worker.close.assert_called_once()
            finally:pool.close()
