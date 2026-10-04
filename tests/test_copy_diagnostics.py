import copy
import unittest
from tools.analyze_copy_diagnostics import checked_search,size_fit
from tools.fight_bench import diagnostic_request


class CopyDiagnosticIntegrity(unittest.TestCase):
    def search(self):
        return {'expanded_nodes':2,'wall_us':1000,'budget_ms':600000,'time_boundary':False,
            'work_metrics':{'counts':{'TransitionCount':5}},'copy_diagnostics':{
                'forks':6,'child_nodes':5,'child_node_fates':{'expanded':1,'layer_retention':4},
                'counted_transitions':5,'unattributed_transitions':0,'expansion_events':2,'root_expansion_events':1,
                'late_snapshot_freezes':0,'measurement_mode':'writes','same_value_store_selftest':True,
                'fork_fates':{'expanded':{'forks':2,'counted_transitions':1,'copies':{'power':4},
                    'actual_written_lifetime':{'power':1},'actual_written_before_snapshot':{'power':0}},
                    'layer_retention':{'forks':4,'counted_transitions':4,'copies':{'power':8},
                    'actual_written_lifetime':{'power':0},'actual_written_before_snapshot':{'power':0}}}}}

    def test_distinct_universes_close_without_treating_root_as_child(self):
        self.assertTrue(all(checked_search(self.search()).values()))
        for key,value in [('forks',5),('child_nodes',6),('counted_transitions',6),
            ('unattributed_transitions',1),('root_expansion_events',0),('same_value_store_selftest',False)]:
            search=self.search();search['copy_diagnostics'][key]=value
            with self.assertRaises(ValueError):checked_search(search)
        search=self.search();search['copy_diagnostics']['fork_fates']['expanded']['actual_written_lifetime']['power']=5
        with self.assertRaises(ValueError):checked_search(search)

    def test_only_explicit_diagnostics_are_masked(self):
        base={'advisor':{'nodes':10000,'solver':'pinned.dll','measure_fork_writes':False},'history':[{'kind':'card'}]}
        for mode in ('writes','costs'):
            changed=copy.deepcopy(base);changed['advisor'].update(measure_fork_writes=True,fork_measurement_mode=mode)
            self.assertEqual(diagnostic_request(base),diagnostic_request(changed))
            changed['advisor']['nodes']=9999
            self.assertNotEqual(diagnostic_request(base),diagnostic_request(changed))
        for value in ('true',1,None):
            changed=copy.deepcopy(base);changed['advisor']['measure_fork_writes']=value
            with self.assertRaises(ValueError):diagnostic_request(changed)
        changed=copy.deepcopy(base);changed['advisor']['fork_measurement_mode']='unknown'
        with self.assertRaises(ValueError):diagnostic_request(changed)

    def test_size_regression_handles_weights_and_rank_failure(self):
        rows=[{'cards':cards,'powers':powers,'forks':cards+powers,'bytes':50+3*cards+5*powers}
            for cards in (10,20)for powers in (1,3)]
        fit=size_fit(rows,'bytes')
        self.assertAlmostEqual(fit['intercept'],50)
        self.assertAlmostEqual(fit['per_card'],3)
        self.assertAlmostEqual(fit['per_power'],5)
        self.assertAlmostEqual(fit['weighted_R2'],1)
        self.assertFalse(size_fit(rows[:2],'bytes')['identifiable'])


if __name__=='__main__':unittest.main()
