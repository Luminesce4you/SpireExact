"""iteration-058 switches, both off by default: root_async (no root barrier) and requeue_lost (no native process)."""
import copy,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from spire_exact.canonical import canonical
from spire_exact.planning.search import SearchConfig,solve,halve_nodes
from spire_exact.planning.gates import preset
from test_focus_search import CTX,FakeEvaluator

LOST={'status':'UNKNOWN','trace':[],'decision_evidence':[],'native_terminal_observed':False,'value':None,'game_equivalence_verified':False}

class LossyEvaluator(FakeEvaluator):
    """The toy world, except that planned dispatches fail the way the pool reports memory losses."""
    plan={}   # (kind, times requeued) -> reasons of the next such dispatches
    log=[]    # (kind, times requeued, request) of every dispatch
    def dispatch(self,specs):
        before=len(self.ready);n=super().dispatch(specs)
        for i in range(before,len(self.ready)):
            spec,_,label=self.ready[i]
            if spec['kind']=='gate_probe':continue
            LossyEvaluator.log.append((spec['kind'],spec.get('requeued',0),copy.deepcopy(spec['request'])))
            reasons=LossyEvaluator.plan.get((spec['kind'],spec.get('requeued',0)))
            if reasons:self.ready[i]=(spec,{**LOST,'reason':reasons.pop(0)},label)
        return n

def run(evaluations=40,workers=2,evaluator=FakeEvaluator,**overrides):
    FakeEvaluator.requests=[];FakeEvaluator.policy_aware=False;FakeEvaluator.map_step=False
    options=dict(evaluations=evaluations,scheduler='focus',dispatch_mode='ordered',dispatch_window=4,repair_mode='gate',prior=True,
                 lookahead_floors=99,lookahead_actions=12000,max_decisions=12000,gate_retry_plans=tuple(preset('escalate')['retries']))
    options.update(overrides)
    advisor={'budget_ms':1000,'boss_budget_ms':1000,'nodes':60000,'gate_plans':preset('escalate')['plans']}
    with tempfile.TemporaryDirectory()as d,patch('spire_exact.planning.search.Evaluator',evaluator):
        pool=SimpleNamespace(stats={},resources=SimpleNamespace(as_dict=lambda:{},workers=workers))
        result=solve(CTX,Path(d),pool,SearchConfig(**options),advisor=advisor)
        json.dumps(result)
        return result,list(FakeEvaluator.requests)

class SwitchTests(unittest.TestCase):
    def test_off_by_default_and_validated(self):
        config=SearchConfig();self.assertEqual((config.root_async,config.requeue_lost),(False,0))
        for bad in (dict(root_async=1),dict(requeue_lost=-1),dict(requeue_lost=True)):
            with self.assertRaises(ValueError):SearchConfig(**bad)
        plain,requests=run(root_policies=('pick','elo'))
        same,again=run(root_policies=('pick','elo'),root_async=False,requeue_lost=0)
        self.assertEqual([canonical(r)for _,r in again],[canonical(r)for _,r in requests])
        self.assertNotIn('requeued_evaluations',plain)

class RootAsyncTests(unittest.TestCase):
    def test_the_first_round_has_root_round_evaluations_and_the_scheduler_waits_for_the_root(self):
        _,requests=run(evaluations=20,workers=5,prior=False,root_policies=('pick','elo'),root_async=True,root_round=5)
        kinds=[k for k,_ in requests]
        self.assertEqual(kinds[:5],['baseline','root_policy','root_policy','restart','restart'])
        # The sixth dispatch follows the absorbed root results: a proposal of the scheduler or a repair.
        self.assertNotEqual(kinds[5],'restart')
        self.assertEqual(len({r['policy_seed']for r in [r for _,r in requests[3:5]]}),2)
        _,barrier=run(evaluations=20,workers=5,prior=False,root_policies=('pick','elo'))
        self.assertEqual([k for k,_ in barrier[:3]],kinds[:3]);self.assertNotEqual(barrier[3][0],'restart')
        # The default round is 7 (the round of the default 7 workers), whatever the number of workers.
        _,default=run(evaluations=20,workers=5,prior=False,root_policies=('pick','elo'),root_async=True)
        self.assertEqual([k for k,_ in default[:7]],['baseline','root_policy','root_policy']+['restart']*4)
        self.assertNotEqual(default[7][0],'restart')
    def test_a_round_smaller_than_the_root_batch_dispatches_the_root_only(self):
        _,requests=run(evaluations=12,workers=2,prior=False,root_policies=('pick','elo'),root_async=True,root_round=2)
        self.assertEqual([k for k,_ in requests[:3]],['baseline','root_policy','root_policy'])
        self.assertNotEqual(requests[3][0],'restart');self.assertEqual(len(requests),12)
        for bad in (0,-1,True):
            with self.assertRaises(ValueError):SearchConfig(root_async=True,root_round=bad)
    def test_the_path_does_not_depend_on_the_number_of_workers(self):
        # iteration-069: the first round used to be max(workers, root batch).
        def path(workers):return [canonical(r)for _,r in run(evaluations=40,workers=workers,root_policies=('pick','elo'),root_async=True)[1]]
        seven=path(7)
        for workers in (1,2,4,5,6,8,16):self.assertEqual(path(workers),seven,workers)
    def test_reproducible(self):
        def once():return [canonical(r)for _,r in run(evaluations=30,workers=4,root_policies=('pick',),root_async=True)[1]]
        self.assertEqual(once(),once())

class RequeueTests(unittest.TestCase):
    def setUp(self):
        LossyEvaluator.log=[]
        LossyEvaluator.plan={('macro_focus',0):['NATIVE_TASK_MEMORY_BUDGET','MEMORY_ADMISSION_DENIED'],
                             ('macro_focus',1):['NATIVE_TASK_MEMORY_BUDGET']*4}
    def test_halve_nodes(self):
        request={'advisor':{'nodes':60000,'normal_nodes':10000,'budget_ms':5,'gate_plans':{'Boss':{'members':[{'nodes':120000,'beam':90}]}}}}
        halve_nodes(request);advisor=request['advisor']
        self.assertEqual((advisor['nodes'],advisor['normal_nodes'],advisor['budget_ms']),(30000,5000,5))
        self.assertEqual(advisor['gate_plans']['Boss']['members'][0],{'nodes':60000,'beam':90})
        halve_nodes({'history':[]})
    def test_lost_proposals_are_lost_without_the_switch(self):
        result,_=run(evaluator=LossyEvaluator)
        self.assertFalse(any(n for _,n,_ in LossyEvaluator.log));self.assertNotIn('requeued_evaluations',result)
    def test_lost_proposals_come_back_once_and_budget_stops_with_half_the_nodes(self):
        with patch('spire_exact.planning.search.REQUEUE_HOLD_SECONDS',0):
            result,_=run(evaluator=LossyEvaluator,requeue_lost=1)
        self.assertEqual(result['requeued_evaluations'],{'NATIVE_TASK_MEMORY_BUDGET':1,'MEMORY_ADMISSION_DENIED':1})
        first=[r for k,n,r in LossyEvaluator.log if k=='macro_focus' and n==0][:2]
        again=[r for k,n,r in LossyEvaluator.log if n==1]
        self.assertEqual(len(again),2);self.assertFalse(any(n>1 for _,n,_ in LossyEvaluator.log))
        self.assertEqual([r['history']for r in again],[r['history']for r in first])
        budget,denied=again
        self.assertEqual(budget['advisor']['nodes'],30000)
        self.assertTrue(all(m['nodes']==60000 for plan in budget['advisor']['gate_plans'].values()for m in plan['members']))
        self.assertEqual(denied['advisor'],first[1]['advisor'])
    def test_admission_denials_wait_before_they_are_dispatched_again(self):
        LossyEvaluator.plan={('macro_focus',0):['MEMORY_ADMISSION_DENIED']}
        with patch('spire_exact.planning.search.REQUEUE_HOLD_SECONDS',3600):
            result,requests=run(evaluator=LossyEvaluator,requeue_lost=2)
        self.assertEqual(result['requeued_evaluations'],{'MEMORY_ADMISSION_DENIED':1})
        self.assertFalse(any(n for _,n,_ in LossyEvaluator.log));self.assertEqual(len(requests),40)

if __name__=='__main__':unittest.main()
