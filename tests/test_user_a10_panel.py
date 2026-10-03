import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from spire_exact.planning.io import write_json
from tools.experiment import version_hash
from tools.run_user_a10_panel import SEEDS, settings, validate_spec, verified_row


class UserA10PanelTests(unittest.TestCase):
    def make_spec(self, root):
        workspace = root / 'experiments/frozen-test'
        (workspace / 'spire_exact').mkdir(parents=True)
        (workspace / 'native').mkdir()
        (workspace / 'spire_exact/example.py').write_text('x=1\n')
        version = version_hash(workspace)
        write_json(workspace / 'freeze.json', {'source_version': version})
        write_json(root / 'ready.json', {'ready_for_user_panel': True, 'version': version})
        write_json(root / 'gate.json', {'promoted': True})
        return {'workspace': str(workspace), 'version': version, 'seeds': SEEDS,
                'run_id': 'panel-test', 'runtime_profile': 'server-one-heap', 'queue_policy': 'fifo',
                'optimization_convergence_record': str(root / 'ready.json'),
                'gates': [{'path': str(root / 'gate.json'), 'field': 'promoted'}]}

    def test_all_six_have_identical_fresh_settings_except_seed(self):
        spec = {'runtime_profile': 'server-one-heap', 'queue_policy': 'fifo'}
        rows = [settings(spec, seed) for seed in SEEDS]
        self.assertEqual([r[1] for r in rows], ['10101010', '101010', '1010', '10', '1', '0'])
        self.assertTrue(all(r[2:] == rows[0][2:] for r in rows))
        self.assertNotIn('--initial-prefix', rows[0])
        self.assertNotIn('--import-checkpoints', rows[0])

    def test_source_edit_after_freeze_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); spec = self.make_spec(root)
            workspace = validate_spec(spec, root)
            (workspace / 'spire_exact/example.py').write_text('x=2\n')
            with self.assertRaisesRegex(ValueError, 'Frozen source'):
                validate_spec(spec, root)

    def test_unready_optimization_and_partial_panel_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); spec = self.make_spec(root)
            write_json(root / 'ready.json', {'ready_for_user_panel': False, 'version': spec['version']})
            with self.assertRaisesRegex(ValueError, 'Convergence'):
                validate_spec(spec, root)
            with self.assertRaisesRegex(ValueError, 'all six'):
                validate_spec(dict(spec, seeds=SEEDS[:-1]), root)

    def test_claimed_win_requires_actual_witness_and_valid_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); folder = root / 'run'; out = root / 'audit'
            write_json(folder / 'baseline-report.json', {'seed': '1', 'verified_win': True, 'exit_code': 0,
                       'wall_seconds': 10, 'resources': {}})
            with patch('tools.audit_a10_baseline.audit'):
                for evidence in ({'evidence_valid': True, 'witness': None},
                                 {'evidence_valid': False, 'witness': {'label': 'bad'}}):
                    write_json(out / 'audit.json', evidence)
                    row = verified_row(folder, out)
                    self.assertFalse(row['verified_win'])
                    self.assertEqual(row['classification'], 'EVIDENCE_AUDIT_ERROR')

    def test_timeout_is_censored_without_calling_seed_unwinnable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); folder = root / 'run'; out = root / 'audit'
            write_json(folder / 'baseline-report.json', {'seed': '0', 'verified_win': False, 'exit_code': 124,
                       'wall_seconds': 10800, 'resources': {'hard_timeout': True}})
            write_json(out / 'audit.json', {'evidence_valid': False, 'witness': None})
            with patch('tools.audit_a10_baseline.audit'):
                row = verified_row(folder, out)
            self.assertEqual(row['classification'], 'TIMEOUT')
            self.assertTrue(row['censored'])
            self.assertFalse(row['verified_win'])
