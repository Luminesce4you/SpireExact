import unittest
from tools.solver_diagnostics import stack_profile,totals


class SolverDiagnosticsTests(unittest.TestCase):
    def test_nested_stacks_exclude_background_and_remove_pseudo_leaf(self):
        frames=[{'name':n}for n in ('CombatSolver.CombatBeamSolver.SolveCore()','Fork()','CPU_TIME','BackgroundWait()')]
        events=[{'type':kind,'at':time,'frame':frame}for kind,time,frame in
                [('O',0,0),('O',1,1),('O',1,2),('C',4,2),('C',4,1),('C',5,0),
                 ('O',5,3),('O',5,2),('C',10,2),('C',10,3)]]
        result=stack_profile({'shared':{'frames':frames},'profiles':[{'name':'worker','type':'evented','unit':'milliseconds','startValue':0,'events':events}]})
        self.assertEqual(result['search_sample_ms'],5)
        leaves={r['name']:r['ms']for r in result['exclusive_last_resolved_frame']}
        self.assertEqual(leaves,{'Fork()':3,'CombatSolver.CombatBeamSolver.SolveCore()':2})
        self.assertNotIn('BackgroundWait()',leaves)
        self.assertEqual(result['sample_kind_ms']['CPU_TIME'],3)

    def test_stack_reader_fails_closed_on_malformed_events(self):
        with self.assertRaisesRegex(ValueError,'Unbalanced'):
            stack_profile({'shared':{'frames':[{'name':'a'}]},'profiles':[{'name':'x','type':'evented','unit':'milliseconds','startValue':0,'events':[{'type':'C','at':1,'frame':0}]}]})

    def test_empty_search_has_no_invented_percentages(self):
        r=stack_profile({'shared':{'frames':[]},'profiles':[{'name':'x','type':'evented','unit':'milliseconds','startValue':0,'events':[]}]})
        self.assertEqual(r['search_sample_ms'],0)
        self.assertEqual(r['inclusive'],[])

    def test_allocation_uses_raw_bytes_and_binary_gib(self):
        row={'search_wall_us_exact':2_000_000,'native_wall_us':4_000_000,'nodes':100,
             'allocated_bytes_exact':2**30,'gc_pause_ms_exact':100,'phase_metrics_exact':{
                 'ForkMetric':{'wall_us':500_000,'allocated_bytes':2**29}},'native_stages':{}}
        result=totals([row])
        self.assertEqual(result['allocation_GiB'],1)
        self.assertEqual(result['allocation_bytes'],2**30)
        self.assertEqual(result['nodes_per_search_second'],50)
        self.assertEqual(result['phases_exclusive']['ForkMetric']['search_fraction'],.25)
        self.assertEqual(result['gc_pause_fraction'],.05)
