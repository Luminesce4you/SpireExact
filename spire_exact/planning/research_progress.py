"""Eligibility checks for opt-in native Progress snapshots.

These checks bind opaque native JSON to one baseline, context and binary. They
do not prove a native restore: the host still deserializes, round-trips and
checks the actual live Progress before restoring a run or deploying a plan.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
import re
from ..canonical import ContractError, canonical

SCHEMA = 'spire-research-progress/v1'
SNAPSHOT_SCHEMA = 'spire-native-progress-snapshot/v1'
_CONTEXT_KEYS = ('seed', 'character', 'ascension', 'unlocks')
_BASELINE_KEYS = {'schema', 'game_sha256', 'native_identity', 'context',
                  'baseline_sha256', 'baseline_snapshot'}
_SNAPSHOT_KEYS = {'schema', 'game_sha256', 'native_identity', 'context',
                  'baseline_sha256', 'native_sha256', 'native_json'}


def _context(context):
    if type(context) is not dict or any(k not in context for k in _CONTEXT_KEYS):
        raise ContractError('RESEARCH_PROGRESS_CONTEXT_REQUIRED')
    if (type(context['seed']) is not str or not context['seed'].strip()
            or type(context['character']) is not str or not context['character']
            or type(context['ascension']) is not int or context['ascension'] < 0
            or context['unlocks'] not in ('all', 'none')):
        raise ContractError('RESEARCH_PROGRESS_CONTEXT_INVALID')
    objective = context.get('objective', 'whole_run_victory/v1')
    information = context.get('information', 'full')
    if type(objective) is dict:
        information = context.get('information', objective.get('information'))
        objective = objective.get('id')
    if objective != 'whole_run_victory/v1' or information != 'full':
        raise ContractError('RESEARCH_PROGRESS_REQUIRES_FULL_BOOLEAN_OBJECTIVE')
    return {**{k:context[k] for k in _CONTEXT_KEYS},
            'information':'full', 'objective':'whole_run_victory/v1'}


def _sha(value, label):
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise ContractError('RESEARCH_PROGRESS_INVALID_SHA256:' + label)
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate native JSON key')
        result[key] = value
    return result


def _constant(_):
    raise ValueError('non-JSON numeric constant')


def _raw_sha(raw, label):
    if type(raw) is not str or not raw:
        raise ContractError('RESEARCH_PROGRESS_NATIVE_JSON_REQUIRED:' + label)
    try:
        # Parsing checks only syntax/object shape. Decimal tokens remain opaque;
        # never dump, normalize, round or reorder the text used by the checksum.
        node = json.loads(raw, object_pairs_hook=_pairs, parse_float=str,
                          parse_int=str, parse_constant=_constant)
        if type(node) is not dict:
            raise ValueError('native save must be an object')
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise ContractError('RESEARCH_PROGRESS_NATIVE_JSON_INVALID:' + label) from error


def _metadata(value, schema, context, identity, game_sha256):
    if type(value) is not dict or value.get('schema') != schema:
        raise ContractError('RESEARCH_PROGRESS_SCHEMA')
    if type(identity) is not dict or not identity:
        raise ContractError('RESEARCH_PROGRESS_NATIVE_IDENTITY_REQUIRED')
    expected_game = _sha(game_sha256 if game_sha256 is not None else identity.get('game_sha256'), 'game')
    if identity.get('game_sha256') != expected_game or value.get('game_sha256') != expected_game:
        raise ContractError('RESEARCH_PROGRESS_GAME_IDENTITY')
    if (type(value.get('native_identity')) is not dict
            or canonical(value['native_identity']) != canonical(identity)):
        raise ContractError('RESEARCH_PROGRESS_NATIVE_IDENTITY')
    if (type(value.get('context')) is not dict
            or canonical(value['context']) != canonical(_context(context))):
        raise ContractError('RESEARCH_PROGRESS_CONTEXT')
    canonical(value)


def require_research_progress(progress, context, identity, game_sha256=None):
    """Validate the initial-baseline protocol and return an isolated copy.

    An empty object is invalid; only the caller's explicit None means off.
    Context accepts mode1's objective object or the native six-field context.
    """
    if type(progress) is not dict or set(progress) != _BASELINE_KEYS:
        raise ContractError('RESEARCH_PROGRESS_BASELINE_FIELDS')
    _metadata(progress, SCHEMA, context, identity, game_sha256)
    if _sha(progress['baseline_sha256'], 'baseline') != _raw_sha(progress['baseline_snapshot'], 'baseline'):
        raise ContractError('RESEARCH_PROGRESS_BASELINE_CHECKSUM')
    return deepcopy(progress)


def checkpoint_progress_matches(payload, progress, context, identity):
    """Check the inner map-checkpoint payload, not a proof of restorability."""
    try:
        baseline = require_research_progress(progress, context, identity)
        if type(payload) is not dict:
            return False
        if (canonical(payload.get('context')) != canonical(_context(context))
                or canonical(payload.get('identity')) != canonical(identity)):
            return False
        snapshot = payload.get('native_progress_snapshot')
        if type(snapshot) is not dict or set(snapshot) != _SNAPSHOT_KEYS:
            return False
        _metadata(snapshot, SNAPSHOT_SCHEMA, context, identity, baseline['game_sha256'])
        if _sha(snapshot['baseline_sha256'], 'baseline') != baseline['baseline_sha256']:
            return False
        return _sha(snapshot['native_sha256'], 'checkpoint') == _raw_sha(snapshot['native_json'], 'checkpoint')
    except (ContractError, KeyError, TypeError, ValueError, UnicodeError, RecursionError):
        return False
