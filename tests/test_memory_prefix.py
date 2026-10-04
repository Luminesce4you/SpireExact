"""Actual file contracts with fake native journals; no gameplay/solver needed."""
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from spire_exact.canonical import canonical
from spire_exact.planning.memory_prefix import recover_completed_prefix
from spire_exact.planning.archive import classify_failure


IDENTITY = {'host_sha256': 'host', 'game_sha256': 'game', 'godot_sha256': 'godot', 'harmony_sha256': 'harmony'}
STAMP = {'host_sha256': 'host', 'inputs': {'dependencies': {'sts2.dll': 'game', 'GodotSharp.dll': 'godot', '0Harmony.dll': 'harmony'}}}


def fixture(root):
    root = Path(root); (root/'data').mkdir()
    a = {'kind': 'map', 'col': 0, 'row': 1}; b = {'kind': 'map', 'col': 0, 'row': 2}
    request = {'seed': '42', 'character': 'IRONCLAD', 'ascension': 10, 'unlocks': 'all', 'command': 'replay',
               'out': str(root/'data'), 'history': [a], 'preserve_completed_prefix': True,
               'advisor': {'nodes': 60000}, 'research_progress': {'fixture': 'baseline'}}
    (root/'request.json').write_text(json.dumps(request, ensure_ascii=False)+'\n', encoding='utf-8')
    (root/'data/identity.json').write_text(json.dumps(IDENTITY), encoding='utf-8')
    payload = {'request': deepcopy(request), 'request_sha256': hashlib.sha256((root/'request.json').read_bytes()).hexdigest(),
               'context': {k: request[k] for k in ('seed', 'character', 'ascension', 'unlocks')} | {
                   'information': 'full', 'objective': 'whole_run_victory/v1'},
               'identity': deepcopy(IDENTITY), 'research_progress': deepcopy(request['research_progress']),
               'status': 'UNKNOWN', 'value': None, 'prefix_length': 2, 'requested_prefix_length': 1,
               'restored_prefix': 1, 'history': [a, b],
               'evidence': [{'phase': 'map', 'available_actions': [a]}, {'phase': 'map', 'available_actions': [b]}],
               'boundary': {'combat_entry': True, 'phase': 'play', 'act': 0, 'floor': 2},
               'observation': {'hp': '80', 'max_hp': '80', 'act': 0, 'floor': 2}}
    return request, payload


def publish(root, payload, *, compressed=False, **changes):
    # Use escaped native-style text to exercise semantic validation separately
    # from byte checksum, including strings which Python normally leaves raw.
    text = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(',', ':'))
    wrapper = {'schema': 'spire-completed-prefix/v1', 'payload': payload, 'payload_canonical': text,
               'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(), **changes}
    raw = json.dumps(wrapper).encode('utf-8')
    path = Path(root)/'data/completed-prefix.json'
    if compressed:
        path = path.with_suffix('.json.gz')
        path.write_bytes(gzip.compress(raw, mtime=0))
    else:
        path.write_bytes(raw)
    return raw


class PrefixRecoveryTests(unittest.TestCase):
    def test_completed_native_cp_and_new_actions_recover_as_unknown_not_loss(self):
        with TemporaryDirectory() as root:
            request, payload = fixture(root); publish(root, payload)
            result, identity = recover_completed_prefix(root, request, STAMP, 'NATIVE_TASK_MEMORY_BUDGET')
            self.assertEqual(identity, IDENTITY)
            self.assertEqual(result['trace'], payload['history'])
            self.assertEqual(result['decision_evidence'], payload['evidence'])
            self.assertEqual(classify_failure(result), 'RESOURCE_LIMIT')
            self.assertEqual(result['status'], 'UNKNOWN')
            self.assertIsNone(result['value'])
            self.assertFalse(result['native_terminal_observed'])
            self.assertEqual(result['checkpoints'], [])

    def test_unicode_canonical_text_checksum_is_not_python_encoder_dependent(self):
        with TemporaryDirectory() as root:
            request, payload = fixture(root); payload['scope'] = '中文 <native> & prefix'; publish(root, payload)
            self.assertIsNotNone(recover_completed_prefix(root, request, STAMP, 'NATIVE_TASK_OUT_OF_MEMORY'))

    def test_gzip_only_losslessly_recovers_original_contract_as_unknown(self):
        with TemporaryDirectory() as root:
            request, payload = fixture(root)
            payload['observation']['note'] = '中文 <native> & prefix\n原始动作'
            publish(root, payload, compressed=True)
            result, identity = recover_completed_prefix(root, request, STAMP, 'NATIVE_TASK_OUT_OF_MEMORY')
            self.assertEqual(identity, IDENTITY)
            self.assertEqual(result['trace'], payload['history'])
            self.assertEqual(result['decision_evidence'], payload['evidence'])
            self.assertEqual(result['observation'], payload['observation'])
            self.assertEqual(result['status'], 'UNKNOWN')
            self.assertEqual(classify_failure(result), 'RESOURCE_LIMIT')
            self.assertIsNone(result['value'])
            self.assertFalse(result['native_terminal_observed'])
            self.assertTrue(result['completed_prefix_recovery']['journal'].endswith('.json.gz'))

    def test_gzip_publication_is_preferred_over_older_legacy_json(self):
        with TemporaryDirectory() as root:
            request, payload = fixture(root); publish(root, payload)
            current = deepcopy(payload)
            current['observation']['hp'] = '73'
            publish(root, current, compressed=True)
            result, _ = recover_completed_prefix(root, request, STAMP, 'NATIVE_TASK_MEMORY_BUDGET')
            self.assertEqual(result['observation'], current['observation'])
            self.assertTrue(result['completed_prefix_recovery']['journal'].endswith('.json.gz'))

    def test_corrupt_or_nonfile_gzip_never_falls_back_to_valid_legacy_json(self):
        for damage in ('truncated', 'crc', 'deflate', 'not_gzip', 'directory'):
            with self.subTest(damage=damage), TemporaryDirectory() as root:
                request, payload = fixture(root); raw = publish(root, payload)
                path = Path(root)/'data/completed-prefix.json.gz'
                if damage == 'directory':
                    path.mkdir()
                else:
                    data = bytearray(gzip.compress(raw, mtime=0))
                    if damage == 'truncated':
                        data = data[:-4]
                    elif damage == 'crc':
                        data[-8] ^= 1
                    elif damage == 'deflate':
                        # Fixed invalid DEFLATE block type, after the 10-byte
                        # gzip header; exercises zlib.error without gameplay.
                        data[10] = (data[10] & ~6) | 6
                    else:
                        data = b'not a gzip journal'
                    path.write_bytes(data)
                self.assertIsNone(recover_completed_prefix(root, request, STAMP, 'NATIVE_TASK_MEMORY_BUDGET'))

    def test_gzip_decoded_size_is_bounded_and_exact_limit_checks_trailer(self):
        with TemporaryDirectory() as root:
            request, payload = fixture(root)
            payload['observation']['note'] = 'repeated native journal ' * 1024
            raw = publish(root, payload, compressed=True)
            path = Path(root)/'data/completed-prefix.json.gz'
            self.assertLess(path.stat().st_size, len(raw) - 1)
            with patch('spire_exact.planning.memory_prefix.MAX_JOURNAL_BYTES', len(raw) - 1):
                self.assertIsNone(recover_completed_prefix(root, request, STAMP, 'NATIVE_TASK_MEMORY_BUDGET'))
            with patch('spire_exact.planning.memory_prefix.MAX_JOURNAL_BYTES', len(raw)):
                self.assertIsNotNone(recover_completed_prefix(root, request, STAMP, 'NATIVE_TASK_MEMORY_BUDGET'))
                path.write_bytes(path.read_bytes()[:-4])
                self.assertIsNone(recover_completed_prefix(root, request, STAMP, 'NATIVE_TASK_MEMORY_BUDGET'))

    def test_gzip_guard_mismatch_still_rejects_instead_of_legacy_fallback(self):
        for mutate in (lambda p: p['context'].update(seed='different'),
                       lambda p: p['identity'].update(game_sha256='different'),
                       lambda p: p['request']['advisor'].update(nodes=30000),
                       lambda p: p['research_progress'].update(fixture='different'),
                       lambda p: p.update(request_sha256='stale'),
                       lambda p: p['evidence'].pop(),
                       lambda p: p['boundary'].update(combat_entry=False)):
            with self.subTest(mutate=mutate), TemporaryDirectory() as root:
                request, payload = fixture(root); publish(root, payload)
                changed = deepcopy(payload); mutate(changed)
                publish(root, changed, compressed=True)
                self.assertIsNone(recover_completed_prefix(root, request, STAMP, 'NATIVE_TASK_MEMORY_BUDGET'))
        with TemporaryDirectory() as root:
            request, payload = fixture(root); publish(root, payload)
            publish(root, payload, compressed=True, sha256='corrupt')
            self.assertIsNone(recover_completed_prefix(root, request, STAMP, 'NATIVE_TASK_MEMORY_BUDGET'))

    def test_invalid_gzip_json_is_unknown_without_legacy_fallback(self):
        for raw in (b'{', b'[]', b'\xff'):
            with self.subTest(raw=raw), TemporaryDirectory() as root:
                request, payload = fixture(root); publish(root, payload)
                (Path(root)/'data/completed-prefix.json.gz').write_bytes(gzip.compress(raw, mtime=0))
                self.assertIsNone(recover_completed_prefix(root, request, STAMP, 'NATIVE_TASK_MEMORY_BUDGET'))

    def test_off_timeout_cancel_and_synthetic_are_never_salvaged(self):
        with TemporaryDirectory() as root:
            request, payload = fixture(root); publish(root, payload)
            for reason in ('NATIVE_TASK_TIMEOUT', 'SEARCH_CANCELLED', 'MEMORY_ADMISSION_DENIED'):
                self.assertIsNone(recover_completed_prefix(root, request, STAMP, reason))
            request['preserve_completed_prefix'] = False
            self.assertIsNone(recover_completed_prefix(root, request, STAMP, 'NATIVE_TASK_MEMORY_BUDGET'))
            request['preserve_completed_prefix'] = True; request['probe'] = {'edits': []}
            self.assertIsNone(recover_completed_prefix(root, request, STAMP, 'NATIVE_TASK_MEMORY_BUDGET'))

    def test_last_trace_without_atomic_completed_journal_is_not_accepted(self):
        with TemporaryDirectory() as root:
            request, _ = fixture(root)
            (Path(root)/'data/trace.jsonl').write_text('{"kind":"map","col":0,"row":2}\n', encoding='utf-8')
            self.assertIsNone(recover_completed_prefix(root, request, STAMP, 'NATIVE_TASK_MEMORY_BUDGET'))

    def test_bad_checksum_incomplete_evidence_and_inflight_boundary_rejected(self):
        for mutate in (lambda p: p.update(prefix_length=3), lambda p: p['evidence'].pop(),
                       lambda p: p['boundary'].update(combat_entry=False), lambda p: p.update(value=[0]),
                       lambda p: p['evidence'][1].update(available_actions=[]),
                       lambda p: p['history'][0].update(col=4), lambda p: p.pop('observation')):
            with self.subTest(mutate=mutate), TemporaryDirectory() as root:
                request, payload = fixture(root); mutate(payload); publish(root, payload)
                self.assertIsNone(recover_completed_prefix(root, request, STAMP, 'NATIVE_TASK_MEMORY_BUDGET'))
        with TemporaryDirectory() as root:
            request, payload = fixture(root); publish(root, payload, sha256='corrupt')
            self.assertIsNone(recover_completed_prefix(root, request, STAMP, 'NATIVE_TASK_MEMORY_BUDGET'))

    def test_context_identity_request_and_progress_are_all_bound(self):
        for mutate in (lambda p: p['context'].update(seed='different'), lambda p: p['identity'].update(game_sha256='different'),
                       lambda p: p['request']['advisor'].update(nodes=30000),
                       lambda p: p['research_progress'].update(fixture='different'), lambda p: p.update(request_sha256='stale')):
            with self.subTest(mutate=mutate), TemporaryDirectory() as root:
                request, payload = fixture(root); mutate(payload); publish(root, payload)
                self.assertIsNone(recover_completed_prefix(root, request, STAMP, 'NATIVE_TASK_MEMORY_BUDGET'))

    def test_safe_map_boundary_can_be_replayed_but_no_new_progress_is_not_queued(self):
        with TemporaryDirectory() as root:
            request, payload = fixture(root)
            payload['boundary'] = {'combat_entry': False, 'quiescent_map': True, 'phase': 'map'}
            publish(root, payload)
            self.assertIsNotNone(recover_completed_prefix(root, request, STAMP, 'NATIVE_TASK_MEMORY_BUDGET'))
            payload.update(history=request['history'], prefix_length=1, evidence=payload['evidence'][:1])
            publish(root, payload)
            self.assertIsNone(recover_completed_prefix(root, request, STAMP, 'NATIVE_TASK_MEMORY_BUDGET'))


if __name__ == '__main__':
    unittest.main()
