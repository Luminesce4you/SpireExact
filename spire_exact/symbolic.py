"""Small audited guarded-command IR. This is a reference language, NOT STS2 rules.

All effects are explicit. No eval(), dynamic code execution, floating point,
heuristic action filtering, or unlabelled STS2 RNG emulation.
"""
from __future__ import annotations
import hashlib
from itertools import product
from typing import Any, Iterator
from .canonical import ContractError, UnsupportedSemantic, canonical, clone, digest, read_json
from .core import Transition, Value, checked_value


def get_path(state: dict[str, Any], path: str) -> Any:
    current: Any = state
    for part in path.split('.'):
        current = current[int(part)] if isinstance(current, list) else current[part]
    return current


def set_path(state: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split('.')
    current: Any = state
    for part in parts[:-1]:
        current = current[int(part)] if isinstance(current, list) else current[part]
    key: str | int = int(parts[-1]) if isinstance(current, list) else parts[-1]
    current[key] = clone(value)


def integer(value: Any) -> int:
    if type(value) is not int:
        raise ContractError('arithmetic requires exact integers')
    return value


def boolean(value: Any) -> bool:
    if type(value) is not bool:
        raise ContractError('guard requires an explicit boolean')
    return value


def expression(expr: Any, state: dict[str, Any], params: dict[str, Any]) -> Any:
    if not isinstance(expr, dict):
        if isinstance(expr, list):
            return [expression(x, state, params) for x in expr]
        return expr
    if len(expr) != 1:
        raise ContractError('expression must contain exactly one operator')
    op, arg = next(iter(expr.items()))
    if op == 'lit':
        return clone(arg)
    if op == 'get':
        return get_path(state, arg)
    if op == 'param':
        return params[arg]
    if op == 'not':
        return not boolean(expression(arg, state, params))
    if op == 'and':
        return all(boolean(expression(x, state, params)) for x in arg)
    if op == 'or':
        return any(boolean(expression(x, state, params)) for x in arg)
    values = [expression(x, state, params) for x in arg]
    if op == 'eq':
        return canonical(values[0]) == canonical(values[1])
    if op == 'in':
        return any(canonical(values[0]) == canonical(item) for item in values[1])
    if op in ('add', 'mul', 'min', 'max', 'sub', 'floordiv', 'le', 'lt', 'ge', 'gt'):
        ints = [integer(x) for x in values]
        if op == 'add': return sum(ints)
        if op == 'mul':
            value = 1
            for item in ints: value *= item
            return value
        if op == 'min': return min(ints)
        if op == 'max': return max(ints)
        if len(ints) != 2:
            raise ContractError(op + ' needs two operands')
        a, b = ints
        if op == 'sub': return a - b
        if op == 'floordiv': return a // b
        if op == 'le': return a <= b
        if op == 'lt': return a < b
        if op == 'ge': return a >= b
        if op == 'gt': return a > b
    raise UnsupportedSemantic('unknown expression: ' + op)


def effects(program: list[dict[str, Any]], state: dict[str, Any], params: dict[str, Any]) -> None:
    for effect in program:
        op = effect['op']
        if op == 'set':
            set_path(state, effect['path'], expression(effect['value'], state, params))
        elif op == 'inc':
            value = integer(get_path(state, effect['path']))
            amount = integer(expression(effect['value'], state, params))
            set_path(state, effect['path'], value + amount)
        elif op == 'if':
            branch = 'then' if boolean(expression(effect['condition'], state, params)) else 'else'
            effects(effect.get(branch, []), state, params)
        elif op == 'assert':
            if not boolean(expression(effect['condition'], state, params)):
                raise ContractError(effect.get('message', 'IR invariant failed'))
        elif op == 'rng_int':
            stream = state['rng'][effect['stream']]
            if stream['algorithm'] != 'reference-lcg32-v1':
                raise UnsupportedSemantic('reference interpreter does not implement native STS2 RNG')
            lo = integer(expression(effect['min'], state, params))
            hi = integer(expression(effect['max'], state, params))
            width = hi - lo + 1
            if not 0 < width <= (1 << 32):
                raise ContractError('rng_int range must be between 1 and 2^32 values')
            threshold = (1 << 32) % width
            while True:
                stream['state'] = (1664525 * integer(stream['state']) + 1013904223) & 0xffffffff
                stream['calls'] += 1
                if stream['state'] >= threshold:
                    break
            set_path(state, effect['path'], lo + stream['state'] % width)
        else:
            raise UnsupportedSemantic('unknown effect: ' + str(op))


class SymbolicModel:
    def __init__(self, specification: dict[str, Any], seed: str = '42') -> None:
        self.spec = clone(specification)
        if self.spec.get('schema') != 'spire-reference-ir/v1':
            raise ContractError('not a supported reference IR specification')
        self.seed = str(seed)
        self._identity = {'backend': 'reference-symbolic-ir', 'backend_version': '1',
                          'model_id': self.spec['model_id'], 'spec_sha256': digest(self.spec),
                          'seed': self.seed, 'is_native_sts2': False}
        ids = [rule['id'] for rule in self.spec['actions']]
        if len(set(ids)) != len(ids):
            raise ContractError('duplicate action rule id')

    @classmethod
    def from_file(cls, path: str, seed: str = '42') -> 'SymbolicModel':
        return cls(read_json(path), seed)

    def identity(self) -> dict[str, Any]:
        return clone(self._identity)

    def initial(self) -> dict[str, Any]:
        state = clone(self.spec['initial'])
        if 'rng' in state or '__context__' in state:
            raise ContractError('rng and __context__ are reserved root fields')
        state['__context__'] = self.identity()
        state['rng'] = {}
        for stream in self.spec.get('rng_streams', []):
            raw = hashlib.sha256((self.seed + '\0' + stream).encode('utf-8')).digest()
            state['rng'][stream] = {'algorithm': 'reference-lcg32-v1',
                                    'state': int.from_bytes(raw[:4], 'little'), 'calls': 0}
        return state

    def terminal_value(self, state: dict[str, Any]) -> Value | None:
        if not boolean(expression(self.spec['terminal'], state, {})):
            return None
        return checked_value([expression(x, state, {}) for x in self.spec['objective']])

    def transitions(self, state: dict[str, Any]) -> Iterator[Transition]:
        if self.terminal_value(state) is not None:
            return
        for rule in self.spec['actions']:
            names = list(rule.get('parameters', {}))
            domains = [expression(rule['parameters'][name], state, {}) for name in names]
            if any(type(domain) is not list for domain in domains):
                raise ContractError('parameter domains must be explicit finite lists')
            for values in product(*domains):
                params = dict(zip(names, values))
                if not boolean(expression(rule.get('when', True), state, params)):
                    continue
                successor = clone(state)
                effects(rule.get('effects', []), successor, params)
                effects(self.spec.get('after_each', []), successor, params)
                yield Transition({'id': rule['id'], 'params': clone(params)}, successor)


class GraphModel:
    """Explicit finite transition-system input, useful for audits and property tests."""
    def __init__(self, graph: dict[str, Any]) -> None:
        self.graph = clone(graph)
        if graph.get('schema') != 'spire-explicit-model/v1':
            raise ContractError('invalid graph model schema')
        if graph['root'] not in graph['states']:
            raise ContractError('missing graph root')
        self._identity = {'backend': 'explicit-graph', 'backend_version': '1',
                          'model_id': graph['model_id'], 'spec_sha256': digest(graph),
                          'is_native_sts2': False}
    def identity(self): return clone(self._identity)
    def initial(self): return {'node': self.graph['root']}
    def terminal_value(self, state):
        return checked_value(self.graph['states'][state['node']].get('value'))
    def transitions(self, state):
        node = self.graph['states'][state['node']]
        for edge in node.get('edges', []):
            if edge['to'] not in self.graph['states']:
                raise ContractError('missing graph destination')
            yield Transition(clone(edge['action']), {'node': edge['to']})
        if not node.get('complete', True):
            raise UnsupportedSemantic(node.get('reason', 'incomplete backend action enumeration'))
