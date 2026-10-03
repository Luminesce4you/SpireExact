"""Conservative exhaustive graph search for deterministic, fully observed models.

No beam, dominance, value-based pruning or depth cutoffs are hidden here.
Closed finite graphs prove a terminal optimum *in the supplied model*.
A budget stop, unsupported action, or unfinished generator cannot prove optimality.
"""
from __future__ import annotations
from collections import deque
from dataclasses import dataclass
from time import perf_counter as monotonic
from typing import Any, Callable, Iterator, Protocol
from .canonical import ContractError, UnsupportedSemantic, canonical, clone, digest

State = dict[str, Any]
Action = dict[str, Any]
Value = tuple[int, ...]

@dataclass(frozen=True)
class Transition:
    action: Action
    state: State

class Model(Protocol):
    def identity(self) -> dict[str, Any]: ...
    def initial(self) -> State: ...
    def terminal_value(self, state: State) -> Value | None: ...
    def transitions(self, state: State) -> Iterator[Transition]: ...

@dataclass(frozen=True)
class Limits:
    max_states: int = 100_000
    max_expansions: int = 100_000
    max_edges: int = 1_000_000
    max_seconds: float | None = None
    def __post_init__(self) -> None:
        for name in ('max_states', 'max_expansions', 'max_edges'):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f'{name} must be a positive integer')
        if self.max_seconds is not None:
            import math
            if not math.isfinite(self.max_seconds) or self.max_seconds <= 0:
                raise ValueError('max_seconds must be finite and positive')

@dataclass
class SearchOutput:
    result: dict[str, Any]
    graph: dict[str, Any]


def checked_value(value: Any) -> Value | None:
    if value is None:
        return None
    if not isinstance(value, (tuple, list)) or not value or any(type(x) is not int for x in value):
        raise ContractError('terminal objective must be a nonempty tuple/list of exact integers')
    return tuple(value)


def solve(model: Model, limits: Limits = Limits(),
          cancel: Callable[[], bool] | None = None) -> SearchOutput:
    start = monotonic()
    identity = clone(model.identity())
    root = clone(model.initial())
    if type(root) is not dict:
        raise ContractError('initial state must be an object')
    nodes: list[dict[str, Any]] = []
    keys: dict[bytes, int] = {}  # Full byte equality, not SHA equality.
    queue: deque[int] = deque()
    objective_size: int | None = None
    best_id: int | None = None
    best_value: Value | None = None
    expanded = 0
    edge_count = 0
    deduplicated = 0
    blockers: list[dict[str, Any]] = []
    stop_reason: str | None = None

    def register(state: State, parent: int | None, action: Action | None) -> int:
        nonlocal objective_size, best_id, best_value
        if type(state) is not dict:
            raise ContractError('successor state must be an object')
        key = canonical(state)
        frozen = clone(state)
        value = checked_value(model.terminal_value(state))
        if canonical(state) != key:
            raise ContractError('terminal_value mutated its input')
        if value is not None:
            if objective_size is not None and len(value) != objective_size:
                raise ContractError('inconsistent objective dimensions')
            objective_size = len(value)
        node_id = len(nodes)
        node = dict(id=node_id, state=frozen, value=list(value) if value is not None else None,
                    complete=value is not None, edges=[], parent=parent, via=clone(action),
                    depth=0 if parent is None else nodes[parent]['depth'] + 1)
        nodes.append(node)
        keys[key] = node_id
        if value is None:
            queue.append(node_id)
        elif best_value is None or value > best_value:
            best_value, best_id = value, node_id
        return node_id

    register(root, None, None)
    while queue and stop_reason is None:
        if cancel and cancel():
            stop_reason = 'cancelled'
            break
        if limits.max_seconds is not None and monotonic() - start >= limits.max_seconds:
            stop_reason = 'time_budget'
            break
        if expanded >= limits.max_expansions:
            stop_reason = 'expansion_budget'
            break
        node_id = queue.popleft()
        node = nodes[node_id]
        state = clone(node['state'])
        before = canonical(state)
        actions: set[bytes] = set()
        expanded += 1
        iterator = None
        try:
            iterator = iter(model.transitions(state))
            while True:
                if cancel and cancel():
                    stop_reason = 'cancelled'
                    break
                if limits.max_seconds is not None and monotonic() - start >= limits.max_seconds:
                    stop_reason = 'time_budget'
                    break
                # At the cap we conservatively refuse to assume the generator is exhausted.
                if edge_count >= limits.max_edges:
                    stop_reason = 'edge_budget'
                    break
                try:
                    transition = next(iterator)
                except StopIteration:
                    if canonical(state) != before:
                        raise ContractError('transitions mutated its input')
                    node['complete'] = True
                    break
                if canonical(state) != before:
                    raise ContractError('transitions mutated its input')
                if type(transition.action) is not dict:
                    raise ContractError('action must be an object')
                action_key = canonical(transition.action)
                if action_key in actions:
                    raise ContractError('duplicate action: deterministic model must return one successor per complete action')
                actions.add(action_key)
                key = canonical(transition.state)
                if key in keys:
                    target = keys[key]
                    deduplicated += 1
                else:
                    if len(nodes) >= limits.max_states:
                        stop_reason = 'state_budget'
                        break
                    target = register(clone(transition.state), node_id, transition.action)
                node['edges'].append({'action': clone(transition.action), 'to': target})
                edge_count += 1
        except UnsupportedSemantic as error:
            blockers.append({'node': node_id, 'reason': str(error)})
        finally:
            if iterator is not None and hasattr(iterator, 'close'):
                iterator.close()
    if canonical(model.identity()) != canonical(identity):
        raise ContractError('model identity changed during search')
    unresolved = [node['id'] for node in nodes if not node['complete']]
    closed = not unresolved and not blockers and stop_reason is None
    terminals = [node['id'] for node in nodes if node['value'] is not None]
    trace: list[dict[str, Any]] = []
    if best_id is not None:
        current = best_id
        while nodes[current]['parent'] is not None:
            parent = nodes[current]['parent']
            trace.append({'from': parent, 'action': nodes[current]['via'], 'to': current})
            current = parent
        trace.reverse()
    result = {
        'schema': 'spire-exact-result/v1',
        'identity': identity,
        'status': ('OPTIMAL_IN_MODEL' if terminals else 'NO_TERMINAL_REACHABLE_IN_MODEL') if closed else 'INCOMPLETE',
        'proof_scope': 'supplied deterministic model; not a real-game equivalence proof',
        'proven_optimal_in_model': closed and bool(terminals),
        'game_equivalence_verified': False,
        'best_value': list(best_value) if best_value is not None else None,
        'upper_bound': list(best_value) if closed and best_value is not None else None,
        'best_terminal': best_id,
        'trace': trace,
        'terminal_states': terminals,  # Keep all distinct exits, NOT only locally best HP.
        'unresolved_nodes': unresolved,
        'stop_reason': stop_reason,
        'unsupported': blockers,
        'statistics': {'states': len(nodes), 'expansions': expanded, 'edges': edge_count,
                       'duplicate_successors': deduplicated,
                       'elapsed_ms': int((monotonic() - start) * 1000)},
    }
    graph = {'schema': 'spire-exact-graph/v1', 'identity': identity, 'root': 0,
             'closed': closed, 'nodes': nodes, 'best_terminal': best_id,
             'best_value': result['best_value']}
    result['graph_sha256'] = digest(graph)
    return SearchOutput(result, graph)
