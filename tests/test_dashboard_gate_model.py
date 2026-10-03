"""Display snapshots are read from existing results, never refitted in the UI."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dashboard import server


class DashboardGateModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.folder=self.root/'experiments/iteration-099/model'
        self.target=self.folder/'seed-42'
        self.target.mkdir(parents=True)
        self.manifest=self.folder/'validation-manifest.json'
        self.manifest.write_text(json.dumps({'run_id':'model','seed':'42','protocol':'A10-seed-v2'}))
        self.result_path=self.target/'result.json'
        self.model={'version':2,'gates':[{'act':2,'ordinal':1,'encounter':'AEONGLASS',
                    'entries':25,'fitted_entries':24,'pairs':10,'fitted_pairs':0,
                    'survived_entries':0,'best_outcome':0.87,'residual_spread':0.15,
                    'top':[['card:BASH',1.5]],'bottom':[['card:ANGER',-0.5]]}],
                    'scope':'same-seed allocation model; never a bound, proof or cross-seed model'}

    def write(self,model):
        doc={'status':'SEARCH_BUDGET','evaluations':[],'elapsed_seconds':123.5}
        if model is not None:doc['gate_models']=model
        self.result_path.write_text(json.dumps(doc),encoding='utf-8')

    def test_snapshot_preserves_raw_values_and_does_not_change_results(self):
        self.write(self.model);original=self.result_path.read_bytes()
        with patch.object(server,'ROOT',self.root):
            repo=server.Repository();d=repo.snapshot('model')['selected']
        self.assertEqual(d['gate_models'],self.model)
        self.assertEqual(d['gate_model_meta']['elapsed_seconds'],123.5)
        self.assertTrue(d['gate_model_meta']['available'])
        self.assertEqual(Path(d['gate_model_meta']['source']),self.result_path)
        self.assertGreater(d['gate_model_meta']['snapshot_at'],0)
        self.assertEqual(self.result_path.read_bytes(),original)
        self.assertEqual(d['wins'],0)

    def test_missing_snapshot_remains_unavailable(self):
        self.write(None)
        with patch.object(server,'ROOT',self.root):d=server.Repository().detail('model')
        self.assertIsNone(d['gate_models'])
        self.assertFalse(d['gate_model_meta']['available'])

    def test_model_only_change_invalidates_repository_cache(self):
        self.write(self.model)
        with patch.object(server,'ROOT',self.root):
            repo=server.Repository();before=repo.detail('model')
            newer={**self.model,'version':3,'learned_option_deltas':9}
            self.write(newer)
            after=repo.detail('model')
        self.assertEqual(before['gate_models']['version'],2)
        self.assertEqual(after['gate_models']['version'],3)
        self.assertEqual(after['evaluations'],before['evaluations'])
