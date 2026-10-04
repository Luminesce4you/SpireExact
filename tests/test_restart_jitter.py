"""iteration-061 jittered tables (off by default): restarts and extra root rollouts get reproducible jittered
tables instead of repeating a root rollout."""
import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from spire_exact.canonical import canonical
from spire_exact.planning.policies import CARDS,POLICIES,jitter
from spire_exact.planning.search import SearchConfig,solve,family_fields
from spire_exact.planning.gates import preset
from test_focus_search import CTX,FakeEvaluator

TOY=('card:BAD','card:GOOD','card:MEH')

def run(evaluations=24,workers=5,policy_aware=False,labels=None,**overrides):
    """Like test_worker_time.run; records and requests come back aligned (FakeEvaluator appends both per dispatch)."""
    FakeEvaluator.requests=[];FakeEvaluator.policy_aware=policy_aware;FakeEvaluator.map_step=False
    options=dict(evaluations=evaluations,scheduler='focus',dispatch_mode='ordered',dispatch_window=4,repair_mode='gate',prior=False,
                 lookahead_floors=99,lookahead_actions=12000,max_decisions=12000,gate_retry_plans=tuple(preset('escalate')['retries']),
                 root_policies=('pick','elo'),root_async=True)
    options.update(overrides)
    advisor={'budget_ms':1000,'boss_budget_ms':1000,'nodes':60000,'gate_plans':preset('escalate')['plans']}
    with tempfile.TemporaryDirectory()as d,patch('spire_exact.planning.search.Evaluator',FakeEvaluator),\
         patch('spire_exact.planning.search.CARDS',labels or CARDS):
        pool=SimpleNamespace(stats={},resources=SimpleNamespace(as_dict=lambda:{},workers=workers))
        result=solve(CTX,Path(d),pool,SearchConfig(**options),advisor=advisor)
        json.dumps(result)
        return result,list(FakeEvaluator.requests)

def expected(family,labels=CARDS,percent=20):
    name,_,tag=family.partition('~')
    return jitter(POLICIES[name],labels,f'0:{tag}',percent)

class TableTests(unittest.TestCase):
    def test_reproducible_bounded_and_different(self):
        base=POLICIES['pick'];a=jitter(base,CARDS,'0:1',20)
        self.assertEqual(a,jitter(base,CARDS,'0:1',20))
        self.assertNotEqual(a,jitter(base,CARDS,'0:2',20))
        self.assertNotEqual(a,jitter(base,CARDS,'1:1',20))
        moved=[l for l in CARDS if a.get(l)!=base.get(l)]
        self.assertTrue(.1*len(CARDS)<len(moved)<.35*len(CARDS),len(moved))
        for label in moved:
            new,old=a.get(label,[0]*len(base.get(label,[0]))),base.get(label,[0])
            self.assertEqual(len(new),len(old))
            self.assertEqual(len({n-o for n,o in zip(new,old)}),1)
            self.assertIn(new[0]-old[0],(-2,-1,1,2))
        self.assertTrue(all(any(row) and all(-8<=t<=8 for t in row) for row in a.values()))
        self.assertEqual(jitter(base,CARDS,'0:1',0),base)
        self.assertEqual(base,POLICIES['pick'])        # the shared table is never changed

    def test_native_gets_a_sparse_table(self):
        table=jitter({},CARDS,'7:3',20)
        self.assertTrue(table and all(len(row)==1 and row[0]in(-2,-1,1,2) for row in table.values()))

    def test_record_fields(self):
        self.assertEqual(family_fields({'family':'pick~restart3'}),{'family':'pick','jitter':'restart3'})
        self.assertEqual(family_fields({'family':'native~root1'}),{'family':'native','jitter':'root1'})
        self.assertEqual(family_fields({'family':'elo'}),{'family':'elo'})
        self.assertEqual(family_fields({'family':None}),{})

class SwitchTests(unittest.TestCase):
    def test_off_by_default_and_validated(self):
        config=SearchConfig()
        self.assertEqual((config.restart_jitter,config.root_jitter,config.jitter_percent),(False,0,20))
        for bad in (dict(restart_jitter=1),dict(root_jitter=-1),dict(root_jitter=True),dict(jitter_percent=0),
                    dict(jitter_percent=101),dict(jitter_percent=True)):
            with self.assertRaises(ValueError):SearchConfig(**bad)

    def test_off_restarts_repeat_the_root_tables(self):
        # The problem: without jitter every restart sends a root table again.
        result,requests=run()
        roots=[canonical(r.get('policy_prior'))for k,r in requests if k in('baseline','root_policy')]
        restarts=[canonical(r.get('policy_prior'))for k,r in requests if k=='restart']
        self.assertTrue(restarts and all(t in roots for t in restarts))
        fresh=result['search_metrics']['fresh_routes']
        self.assertEqual(fresh['restart']['repeats'],fresh['restart']['rollouts'])   # the toy world ignores tiers here
        self.assertNotIn('jittered_tables',result)

class RestartJitterTests(unittest.TestCase):
    def test_restarts_get_distinct_tables_that_their_lineage_keeps(self):
        result,requests=run(evaluations=40,restart_jitter=True)
        rows=list(zip(result['evaluations'],requests))
        roots=[canonical(r.get('policy_prior'))for _,(k,r) in rows if k in('baseline','root_policy')]
        restarts=[(rec,r)for rec,(k,r) in rows if k=='restart']
        self.assertGreaterEqual(len(restarts),3)
        tables=[canonical(r['policy_prior'])for _,r in restarts]
        self.assertEqual(len(set(tables)),len(tables))
        self.assertFalse(set(tables)&set(roots))
        for rec,r in restarts:
            self.assertTrue(rec['family'].partition('~')[2].startswith('restart'))
            self.assertEqual(r['policy_prior'],expected(rec['family']))
        # Evaluations derived from a restart keep its jittered table (the toy world
        # only grows lineages from restarts that left the root routes).
        result,requests=run(evaluations=40,policy_aware=True,labels=TOY,restart_jitter=True,jitter_percent=100)
        derived=[(rec,r)for rec,(k,r) in zip(result['evaluations'],requests)if k!='restart' and '~'in(rec.get('family')or'')]
        self.assertTrue(derived)
        for rec,r in derived:self.assertEqual(r['policy_prior'],expected(rec['family'],TOY,100))

    def test_reproducible(self):
        def once():return [canonical(r)for _,r in run(evaluations=30,restart_jitter=True,root_jitter=3)[1]]
        self.assertEqual(once(),once())

    def test_jittered_restarts_leave_the_root_routes(self):
        # In a world that follows its tiers, jittered restarts reach routes the root rollouts did not.
        off,_=run(evaluations=30,policy_aware=True,labels=TOY)
        on,_=run(evaluations=30,policy_aware=True,labels=TOY,restart_jitter=True,jitter_percent=100)
        a,b=off['search_metrics']['fresh_routes']['restart'],on['search_metrics']['fresh_routes']['restart']
        self.assertEqual(a['repeats'],a['rollouts'])
        self.assertLess(b['repeats'],b['rollouts'])

class RootJitterTests(unittest.TestCase):
    def test_after_the_root_barrier(self):
        result,requests=run(evaluations=30,root_async=False,root_jitter=4)
        kinds=[k for k,_ in requests]
        self.assertEqual(kinds[:7],['baseline','root_policy','root_policy']+['root_jitter']*4)
        rows=list(zip(result['evaluations'],requests))
        jittered=[(rec,r)for rec,(k,r) in rows if k=='root_jitter']
        self.assertEqual([rec['family']for rec,_ in jittered],['native~root1','pick~root2','elo~root3','native~root4'])
        roots=[canonical(r.get('policy_prior'))for _,(k,r) in rows if k in('baseline','root_policy')]
        tables=[canonical(r['policy_prior'])for _,r in jittered]
        self.assertEqual(len(set(tables)),4);self.assertFalse(set(tables)&set(roots))
        for rec,r in jittered:
            self.assertEqual(r['history'],[]);self.assertEqual(r['policy_prior'],expected(rec['family']))
        self.assertEqual(result['jittered_tables']['root_rollouts'],4)

    def test_fills_the_idle_lanes_of_the_root_round(self):
        # root_async with a first round of 5: the root batch and two jittered roots, no restart;
        # the other two jittered roots go first once the root results are absorbed.
        _,requests=run(evaluations=30,workers=5,root_jitter=4,root_round=5)
        kinds=[k for k,_ in requests]
        self.assertEqual(kinds[:7],['baseline','root_policy','root_policy']+['root_jitter']*4)
        self.assertNotIn('restart',kinds[:7])

    def test_off_unchanged(self):
        same=[canonical(r)for _,r in run(evaluations=30)[1]]
        again=[canonical(r)for _,r in run(evaluations=30,root_jitter=0,restart_jitter=False,jitter_percent=50)[1]]
        self.assertEqual(same,again)

    def test_jittered_roots_leave_the_root_routes(self):
        result,_=run(evaluations=30,policy_aware=True,labels=TOY,root_jitter=6,jitter_percent=100)
        fresh=result['search_metrics']['fresh_routes']['root_jitter']
        self.assertEqual(fresh['rollouts'],6);self.assertLess(fresh['repeats'],6)

if __name__=='__main__':unittest.main()
