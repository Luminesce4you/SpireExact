import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from spire_exact.canonical import ContractError, write_json
from spire_exact.native import compare_exports, game_data, input_fingerprint
from spire_exact.upstream import doctor


class NativeToolTests(unittest.TestCase):
    def test_runtime_without_sdk_is_not_ready(self):
        with patch('spire_exact.upstream.shutil.which', return_value='dotnet'), \
                patch('spire_exact.upstream.run', return_value=''):
            result = doctor()
        self.assertTrue(any('no SDK' in issue for issue in result['issues']))
        self.assertFalse(result['native_dependencies_found'])

    def test_explicit_game_path_and_ambiguity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = root / 'data_sts2_windows_x86_64'
            data.mkdir()
            (data / 'sts2.dll').write_bytes(b'game')
            self.assertEqual(game_data(root), data.resolve())
            (root / 'sts2.dll').write_bytes(b'another')
            with self.assertRaises(ContractError):
                game_data(root)

    def test_missing_dependency_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ContractError):
                input_fingerprint(Path(temp))

    def test_game_update_invalidates_proofs_even_with_same_api(self):
        with tempfile.TemporaryDirectory() as temp:
            a, b = Path(temp) / 'old', Path(temp) / 'new'
            for folder, sha in ((a, 'before'), (b, 'after')):
                write_json(folder / 'api.json', {'types': [{'name': 'Card', 'members': ['cost']}]})
                write_json(folder / 'identity.json', {'game_sha256': sha, 'host_sha256': 'same'})
            result = compare_exports(a, b)
            self.assertEqual(result['status'], 'REVALIDATION_REQUIRED')
            self.assertEqual(result['api_removed'], [])
            self.assertFalse(result['reuse_old_gameplay_proofs'])
            identical = compare_exports(a, a)
            self.assertTrue(identical['matching_binary_inputs'])
            self.assertFalse(identical['reuse_old_gameplay_proofs'])

    def test_card_and_api_deltas(self):
        with tempfile.TemporaryDirectory() as temp:
            a, b = Path(temp) / 'old', Path(temp) / 'new'
            for folder, member, cards in ((a, 'before', [{'id': 'strike', 'energy': 1}, {'id': 'gone'}]),
                                          (b, 'after', [{'id': 'strike', 'energy': 2}, {'id': 'new'}])):
                write_json(folder / 'api.json', {'types': [{'name': 'Card', 'members': [member]}]})
                write_json(folder / 'identity.json', {'game_sha256': member})
                write_json(folder / 'catalog.json', {'cards': cards})
            result = compare_exports(a, b)
            self.assertEqual(result['api_removed'], ['Card::before'])
            self.assertEqual(result['cards_changed'], ['strike'])
            self.assertEqual(result['cards_added'], ['new'])
            self.assertEqual(result['cards_removed'], ['gone'])
