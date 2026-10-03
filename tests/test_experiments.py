import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('experiment',Path(__file__).resolve().parents[1]/'tools/experiment.py')
experiment=importlib.util.module_from_spec(spec);spec.loader.exec_module(experiment)

class ProtocolTests(unittest.TestCase):
    def test_seed_sets_reproduce_and_exclude_every_previous_seed(self):
        first=experiment.seed_set('round-A',100,{'0','1','2','42','43'})
        self.assertEqual(len(set(first)),100)
        self.assertEqual(first,experiment.seed_set('round-A',100,{'0','1','2','42','43'}))
        second=experiment.seed_set('round-B',100,set(first)|{'0','1','2','42','43'})
        self.assertFalse(set(first)&set(second))
    def test_wilson_not_point_estimate_or_certainty(self):
        lower,upper=experiment.wilson(95,100)
        self.assertAlmostEqual(lower,.888249530,places=6)
        self.assertAlmostEqual(upper,.978456320,places=6)
        self.assertLess(experiment.wilson(100,100)[0],1)
        self.assertGreater(experiment.wilson(0,100)[1],0)
    def test_empty_test_has_no_confidence_interval(self):
        self.assertIsNone(experiment.wilson(0,0))

if __name__=='__main__':unittest.main()
