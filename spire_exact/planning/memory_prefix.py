"""Recover only a native completed, pre-combat action prefix after resource loss.

No state restore or feasibility claim: a fresh worker must replay every action.
The executing-action trace is deliberately never used as a salvage journal.
"""
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import zlib
from ..canonical import canonical, ContractError
from .io import read_json

RESOURCE_REASONS = ('NATIVE_TASK_MEMORY_BUDGET', 'NATIVE_TASK_OUT_OF_MEMORY')
MAX_JOURNAL_BYTES = 64 * 1024 * 1024


def recover_completed_prefix(directory, request, stamp, reason):
    """(UNKNOWN candidate, actual identity) or None. Never trust hashes alone."""
    if (not request.get('preserve_completed_prefix') or reason not in RESOURCE_REASONS
            or request.get('probe') or request.get('card_menu_probe')):
        return None
    directory = Path(directory)
    try:
        compressed = directory / 'data/completed-prefix.json.gz'
        # An existing gzip is the latest atomic publication, even if unreadable.
        # Never fall back to a stale legacy journal when that publication fails.
        path = compressed if compressed.exists() else directory / 'data/completed-prefix.json'
        if not path.is_file() or path.stat().st_size > MAX_JOURNAL_BYTES:
            return None
        opener = gzip.open if path.suffix == '.gz' else open
        with opener(path, 'rb') as stream:
            # Limit decompressed bytes, not just the compressed file size. For
            # accepted lengths read through EOF to validate gzip CRC and trailer.
            raw = stream.read(MAX_JOURNAL_BYTES + 1)
        if len(raw) > MAX_JOURNAL_BYTES:
            return None
        wrapper = json.loads(raw.decode('utf-8-sig'))
        if not isinstance(wrapper, dict) or wrapper.get('schema') != 'spire-completed-prefix/v1':
            return None
        payload, text = wrapper.get('payload'), wrapper.get('payload_canonical')
        if (not isinstance(payload, dict) or not isinstance(text, str)
                or hashlib.sha256(text.encode('utf-8')).hexdigest() != wrapper.get('sha256')
                or canonical(json.loads(text)) != canonical(payload)):
            return None
        request_path = directory / 'request.json'
        if hashlib.sha256(request_path.read_bytes()).hexdigest() != payload.get('request_sha256'):
            return None
        if canonical(read_json(request_path)) != canonical(request) or canonical(payload.get('request')) != canonical(request):
            return None
        context = {key: request[key] for key in ('seed', 'character', 'ascension', 'unlocks')} | {
            'information': 'full', 'objective': 'whole_run_victory/v1'}
        if canonical(payload.get('context')) != canonical(context):
            return None
        identity = read_json(directory / 'data/identity.json')
        if canonical(identity) != canonical(payload.get('identity')) or identity.get('host_sha256') != stamp['host_sha256']:
            return None
        for key, name in (('game_sha256', 'sts2.dll'), ('godot_sha256', 'GodotSharp.dll'), ('harmony_sha256', '0Harmony.dll')):
            if identity.get(key) != stamp['inputs']['dependencies'][name]:
                return None
        if canonical(payload.get('research_progress')) != canonical(request.get('research_progress')):
            return None
        history, evidence = payload.get('history'), payload.get('evidence')
        requested = request.get('history')
        if (not isinstance(history, list) or not isinstance(evidence, list) or not isinstance(requested, list)
                or type(payload.get('prefix_length')) is not int or payload['prefix_length'] != len(history)
                or payload.get('requested_prefix_length') != len(requested)
                or len(history) <= len(requested) or len(evidence) != len(history)
                or canonical(history[:len(requested)]) != canonical(requested)
                or any(not isinstance(row, dict) or not isinstance(action, dict)
                       or not isinstance(row.get('available_actions'), list)
                       or not any(canonical(a) == canonical(action) for a in row['available_actions'])
                       for action, row in zip(history, evidence))):
            return None
        boundary = payload.get('boundary')
        if (not isinstance(boundary, dict) or not(boundary.get('combat_entry') is True or boundary.get('quiescent_map') is True)
                or payload.get('status') != 'UNKNOWN' or payload.get('value') is not None
                or not isinstance(payload.get('observation'), dict)):
            return None
        return ({'schema': 'spire-native-decision/v1', 'status': 'UNKNOWN', 'reason': reason,
                 'value': None, 'native_terminal_observed': False, 'game_equivalence_verified': False,
                 'complete': False, 'known_menu_enumerated': False, 'theoretical_upper_bound': 1,
                 'trace': deepcopy(history), 'decision_evidence': deepcopy(evidence),
                 'observation': deepcopy(payload['observation']), 'checkpoints': [], 'performance': None,
                 'completed_prefix_recovery': {'validated': True, 'prefix_length': len(history),
                                               'journal': str(path), 'sha256': wrapper['sha256'],
                                               'scope': 'completed native prefix only; fresh replay required; UNKNOWN, never a loss or cached state'}},
                identity)
    except (OSError, EOFError, zlib.error, ValueError, KeyError, TypeError, ContractError):
        return None
