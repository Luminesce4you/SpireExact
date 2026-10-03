import threading,unittest
from concurrent.futures import CancelledError
from spire_exact.planning.pool import PriorityWorkQueue


class NativeWorkQueueTests(unittest.TestCase):
    def test_priority_changes_execution_but_not_future_identity(self):
        started=threading.Event();release=threading.Event();order=[]
        def first():started.set();release.wait(5);order.append('first');return 'first'
        def work(label):order.append(label);return label
        q=PriorityWorkQueue(1)
        try:
            a=q.submit(0,first);self.assertTrue(started.wait(5))
            b=q.submit(10,work,'late');c=q.submit(1,work,'urgent')
            release.set()
            self.assertEqual([f.result(5)for f in(a,b,c)],['first','late','urgent'])
            self.assertEqual(order,['first','urgent','late'])
        finally:release.set();q.shutdown()
    def test_cancelled_pending_work_never_runs(self):
        started=threading.Event();release=threading.Event();ran=[]
        def first():started.set();release.wait(5)
        q=PriorityWorkQueue(1)
        try:
            active=q.submit(0,first);self.assertTrue(started.wait(5))
            canceled=q.submit(-1,lambda:ran.append('canceled'))
            survivor=q.submit(2,lambda:ran.append('survivor'))
            self.assertTrue(canceled.cancel());release.set();active.result(5);survivor.result(5)
            with self.assertRaises(CancelledError):canceled.result()
            self.assertEqual(ran,['survivor'])
        finally:release.set();q.shutdown()
    def test_shutdown_cancels_public_futures_and_errors_are_delivered(self):
        q=PriorityWorkQueue(1)
        def fail():raise ValueError('native failure')
        with self.assertRaisesRegex(ValueError,'native failure'):q.submit(0,fail).result(5)
        started=threading.Event();release=threading.Event()
        def first():started.set();release.wait(5)
        active=q.submit(0,first);self.assertTrue(started.wait(5))
        pending=q.submit(1,lambda:42)
        q.shutdown(wait=False,cancel_futures=True)
        try:
            self.assertTrue(pending.cancelled())
            with self.assertRaises(RuntimeError):q.submit(0,lambda:0)
        finally:release.set();active.result(5);q.shutdown()
