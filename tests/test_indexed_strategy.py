import copy,unittest
from tests.test_strategy import trajectory
from spire_exact.planning.strategy import StrategicScheduler
from spire_exact.planning.indexed_strategy import IndexedStrategicScheduler,PrefixTrie

class IndexedStrategyTests(unittest.TestCase):
    def test_exact_order_after_room_and_source_priorities_change(self):
        old=StrategicScheduler();new=IndexedStrategicScheduler()
        root=trajectory()
        for s in(old,new):s.add(copy.deepcopy(root),'root')
        for i in range(100):
            a,b=old.next(),new.next();self.assertEqual(a,b)
            if a is None:break
            if i%7==0:
                child=copy.deepcopy(root);child['trace'][a['index']]=copy.deepcopy(a['prefix'][-1])
                for s in(old,new):s.add(copy.deepcopy(child),f'child-{i}')
    def test_equal_digest_does_not_define_prefix_identity(self):
        trie=PrefixTrie();a=trie.index([{'id':1,'pile':[1,2]}]);b=trie.index([{'id':1,'pile':[2,1]}])
        self.assertNotEqual(a[-1],b[-1]);self.assertEqual(trie.restore(a[-1]),[{'id':1,'pile':[1,2]}])
        self.assertEqual(a,trie.index([{'pile':[1,2],'id':1}]))
    def test_no_complete_native_result_is_retained_by_branch(self):
        s=IndexedStrategicScheduler();r=trajectory();s.add(r,'root')
        b=next(iter(s.branches.values()));self.assertFalse(hasattr(b,'result'))
        r['trace'][0]['index']=999
        self.assertNotEqual(s.next()['prefix'][0]['index'],999)

if __name__=='__main__':unittest.main()
