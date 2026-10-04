"""Progress readers preserve partial probe work without certifying exact totals."""
import json
import tempfile
import unittest
from pathlib import Path
from tools.baseline_metrics import summarize_runtime,summarize_work
from tools.seed_progress import summarize


def legacy():
    return {'label':'real','kind':'macro_focus','classification':'NATIVE_ROUTE_DEATH',
        'observation':{'floor':17,'room':'Boss'},'completed_wall_seconds':10,
        'expanded_combat_nodes':20,'performance':{'wall_us':2000000,'cpu_us':1000000},
        'advisor_metrics':{'searches':[{'expanded_nodes':20,'wall_us':1500000}]}}


def partial():
    return {'label':'paired','kind':'paired_card_probe','classification':'SYNTHETIC_PROBE',
        'observation':None,'completed_wall_seconds':20,'expanded_combat_nodes':None,
        'probe_search_seconds':None,'probe_native_seconds':None,
        'measured_expanded_combat_nodes':30,'measured_probe_search_seconds':2.5,
        'measured_probe_native_seconds':3.75,'unknown_work_probes':1,'work_measurement_complete':False}


class ProbeWorkReaderTests(unittest.TestCase):
    def progress(self,rows):
        with tempfile.TemporaryDirectory()as folder:
            root=Path(folder)
            (root/'evaluations.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows),encoding='utf-8')
            return summarize(root)
    def test_fully_measured_legacy_values_stay_unchanged(self):
        report=summarize_runtime([legacy()],{})
        self.assertEqual(report['combat_nodes'],20)
        self.assertEqual(report['native_service_seconds_sum'],2)
        self.assertEqual(report['search_wall_seconds_sum'],1.5)
        self.assertTrue(report['combat_nodes_measurement_complete'])
        self.assertTrue(report['native_service_measurement_complete'])
        self.assertTrue(report['search_wall_measurement_complete'])
        progress=self.progress([legacy()])
        self.assertEqual(progress['expanded_combat_nodes'],20)
        self.assertEqual(progress['native_task_wall_seconds'],2)
        self.assertTrue(progress['combat_nodes_measurement_complete'])
    def test_partial_probe_measured_nodes_and_wall_are_never_lost(self):
        rows=[legacy(),partial()]
        report=summarize_runtime(rows,{})
        self.assertEqual(report['combat_nodes'],50)
        self.assertEqual(report['native_service_seconds_sum'],5.75)
        self.assertEqual(report['search_wall_seconds_sum'],4)
        self.assertEqual(report['by_kind']['paired_card_probe']['nodes'],30)
        self.assertEqual(report['by_kind']['paired_card_probe']['native_seconds'],3.75)
        self.assertFalse(report['combat_nodes_measurement_complete'])
        self.assertEqual(report['combat_nodes_unknown_rows'],1)
        self.assertEqual(report['unknown_work_probes'],1)
        self.assertEqual(report['unknown_work_rows'],1)
        progress=self.progress(rows)
        self.assertEqual(progress['expanded_combat_nodes'],50)
        self.assertEqual(progress['native_task_wall_seconds'],5.8)
        self.assertFalse(progress['combat_nodes_measurement_complete'])
        self.assertEqual(progress['unknown_work_probes'],1)
    def test_cache_hit_does_not_double_count_measured_or_legacy_work(self):
        original=[legacy(),partial()]
        cached=[dict(row,cache_hit=True)for row in original]
        a=summarize_work(original);b=summarize_work(original+cached)
        self.assertEqual(a,b)
        self.assertEqual(self.progress(original)['expanded_combat_nodes'],
                         self.progress(original+cached)['expanded_combat_nodes'])
    def test_completely_missing_metrics_are_unknown_not_exact_zero(self):
        row={'label':'lost','kind':'paired_card_probe','classification':'SYNTHETIC_PROBE',
             'observation':None,'completed_wall_seconds':20,'unknown_work_probes':6,
             'work_measurement_complete':False}
        for report in (summarize_runtime([row],{}),self.progress([row])):
            self.assertEqual(report['combat_nodes'],0)
            self.assertFalse(report['combat_nodes_measurement_complete'])
            self.assertEqual(report['combat_nodes_unknown_rows'],1)
            self.assertEqual(report['unknown_work_probes'],6)
            self.assertFalse(report['native_service_measurement_complete'])
            self.assertFalse(report['search_wall_measurement_complete'])
        self.assertIsNone(self.progress([row])['furthest_floor'])
    def test_measured_subset_takes_precedence_without_double_adding_total(self):
        row=partial();row['expanded_combat_nodes']=10
        report=summarize_runtime([row],{})
        self.assertEqual(report['combat_nodes'],30)
        self.assertFalse(report['combat_nodes_measurement_complete'])


if __name__=='__main__':unittest.main()
