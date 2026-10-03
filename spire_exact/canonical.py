"""Exact JSON identities. Digests are labels, never the equality criterion."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from typing import Any

class ContractError(ValueError):
    pass

class UnsupportedSemantic(RuntimeError):
    pass

def validate_json(value: Any, path: str = '$') -> None:
    if value is None or type(value) in (bool, int, str):
        return
    if type(value) is list:
        for i, item in enumerate(value):
            validate_json(item, f'{path}[{i}]')
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ContractError(f'{path}: object keys must be strings')
            validate_json(item, f'{path}.{key}')
        return
    raise ContractError(f'{path}: unsupported {type(value).__name__}; encode decimals exactly, not as floats')

def canonical(value: Any) -> bytes:
    validate_json(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')

def clone(value: Any) -> Any:
    return json.loads(canonical(value))

def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()

def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f'duplicate JSON key: {key}')
        result[key] = value
    return result

def loads(text: str | bytes) -> Any:
    value = json.loads(text, object_pairs_hook=_object)
    validate_json(value)
    return value

def read_json(path: str | Path) -> Any:
    return loads(Path(path).read_text(encoding='utf-8-sig'))

def write_json(path: str | Path, value: Any) -> None:
    validate_json(value)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(target)
