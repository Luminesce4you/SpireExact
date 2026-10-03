import tempfile,unittest
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from spire_exact.planning.search import Evaluator,SearchConfig

class StreamingTests(unittest.TestCase):
    def test_ordered_collection_is_independent_of_completion_order(self):
        for reverse in (False,True):
            first,second=Future(),Future();pending=iter([first,second])
            pool=SimpleNamespace(inputs={},validate_inputs=lambda r:None,submit=lambda *a:next(pending))
            ctx={'seed':'0','character':'IRONCLAD','ascension':10,'unlocks':'all'}
            with tempfile.TemporaryDirectory()as d:
                ev=Evaluator(pool,ctx,Path(d),SearchConfig(dispatch_mode='ordered'))
                ev.dispatch([{'kind':'first','request':ev.request([])},{'kind':'second','request':ev.request([{'a':1}])}])
                later={'status':'BUDGET','trace':[{'a':1}]};earlier={'status':'BUDGET','trace':[]}
                if reverse:
                    second.set_result((later,None));self.assertEqual(ev.collect(timeout=0,ordered=True),[])
                    first.set_result((earlier,None))
                else:
                    first.set_result((earlier,None));second.set_result((later,None))
                self.assertEqual(ev.collect(timeout=0,ordered=True)[0][0]['kind'],'first')
                self.assertEqual(ev.collect(timeout=0,ordered=True)[0][0]['kind'],'second')
                self.assertEqual(ev.pending_count,0)

    def test_completed_lane_is_collected_without_waiting_for_slow_earlier_lane(self):
        slow,fast=Future(),Future();pending=iter([slow,fast])
        pool=SimpleNamespace(inputs={},validate_inputs=lambda r:None,submit=lambda *a:next(pending))
        ctx={'seed':'0','character':'IRONCLAD','ascension':0,'unlocks':'all'}
        with tempfile.TemporaryDirectory() as d:
            ev=Evaluator(pool,ctx,Path(d),SearchConfig())
            specs=[{'kind':'slow','request':ev.request([])},{'kind':'fast','request':ev.request([{'a':1}])}]
            ev.dispatch(specs)
            fast.set_result(({'status':'BUDGET','trace':[{'a':1}]},None))
            got=ev.collect(timeout=0)
            self.assertEqual([x[0]['kind'] for x in got],['fast']);self.assertEqual(ev.pending_count,1)
            self.assertFalse(slow.done())
            slow.set_result(({'status':'BUDGET','trace':[]},None));self.assertEqual(len(ev.collect(timeout=0)),1)
            self.assertEqual(ev.pending_count,0)
    def test_cancelled_future_is_unknown_and_not_a_dead_end(self):
        f=Future();pool=SimpleNamespace(inputs={},validate_inputs=lambda r:None,submit=lambda *a:f)
        ctx={'seed':'0','character':'IRONCLAD','ascension':0,'unlocks':'all'}
        with tempfile.TemporaryDirectory() as d:
            ev=Evaluator(pool,ctx,Path(d),SearchConfig())
            ev.dispatch([{'kind':'cancelled','request':ev.request([])}]);f.cancel()
            _,result,_=ev.collect(timeout=0)[0]
            self.assertEqual(result['status'],'UNKNOWN');self.assertEqual(result['reason'],'SEARCH_CANCELLED')
            self.assertFalse(result['native_terminal_observed'])

if __name__=='__main__':unittest.main()
