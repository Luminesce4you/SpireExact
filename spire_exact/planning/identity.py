"""Bind proposal results to the actual advisor and dependency bytes.

The native game's own identity is separate. A cached proposal is not portable
across replacing a mirror DLL at the same filesystem path.
"""
from pathlib import Path
from ..native import file_hash
from ..canonical import canonical, ContractError


def advisor_identity(config: dict) -> dict:
    files = {Path(config[name]).resolve() for name in ('solver', 'harness')}
    for directory in config.get('dependency_dirs', []):
        files.update(path.resolve() for path in Path(directory).glob('*.dll'))
    return {str(path): file_hash(path) for path in sorted(files)}


def require_advisor_identity(config: dict) -> None:
    expected = config.get('binary_identity')
    if expected is None:
        raise ContractError('ADVISOR_IDENTITY_REQUIRED: build config with NativeCampaignBackend')
    if canonical(expected) != canonical(advisor_identity(config)):
        raise ContractError('ADVISOR_INPUTS_CHANGED: retire the worker and rebuild before reuse')
