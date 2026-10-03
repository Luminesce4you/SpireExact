"""A separate exhaustive-closure checker; never trusts the search's complete flag alone."""
from __future__ import annotations
from collections import deque
from typing import Any
from .canonical import ContractError, UnsupportedSemantic, canonical, clone
from .core import Model, checked_value

def verify_graph(model: Model, certificate: dict[str, Any]) -> dict[str, Any]:
    def fail(message: str) -> None:
        raise ContractError('certificate rejected: ' + message)
    if certificate.get('schema') != 'spire-exact-graph/v1':
        fail('wrong schema')
    if canonical(certificate.get('identity')) != canonical(model.identity()):
        fail('model/version/seed identity mismatch')
    if certificate.get('closed') is not True:
        fail('search graph is not closed')
    nodes = certificate.get('nodes')
    if not isinstance(nodes, list) or not nodes or certificate.get('root') != 0:
        fail('missing root')
    if any(n.get('id') != i for i, n in enumerate(nodes)):
        fail('invalid node numbering')
    if canonical(nodes[0]['state']) != canonical(model.initial()):
        fail('initial state mismatch')
    keys: dict[bytes, int] = {}
    for node in nodes:
        key = canonical(node['state'])
        if key in keys:
            fail('duplicate states')
        keys[key] = node['id']
    best_value = None
    terminal_values: dict[int, tuple[int, ...]] = {}
    dimension = None
    for node in nodes:
        if node.get('complete') is not True:
            fail(f'unexpanded node {node["id"]}')
        state = clone(node['state'])
        before = canonical(state)
        value = checked_value(model.terminal_value(state))
        if canonical(state) != before:
            fail('model terminal evaluation mutated input')
        declared = checked_value(node.get('value'))
        if value != declared:
            fail('terminal value mismatch')
        if value is not None:
            if dimension is not None and len(value) != dimension:
                fail('objective dimensions differ')
            dimension = len(value)
            if node['edges']:
                fail('terminal node has outgoing edges')
            terminal_values[node['id']] = value
            best_value = value if best_value is None or value > best_value else best_value
            continue
        actual: dict[bytes, bytes] = {}
        try:
            for transition in model.transitions(state):
                action = canonical(transition.action)
                if action in actual:
                    fail('nondeterministic or duplicate action')
                target = canonical(transition.state)
                if target not in keys:
                    fail('legal successor missing from certificate')
                actual[action] = target
                if canonical(state) != before:
                    fail('model transitions mutated input')
        except UnsupportedSemantic as error:
            fail('unsupported semantics: ' + str(error))
        if canonical(state) != before:
            fail('model transitions mutated input')
        recorded: dict[bytes, bytes] = {}
        for edge in node['edges']:
            destination = edge.get('to')
            if type(destination) is not int or not 0 <= destination < len(nodes):
                fail('bad destination')
            action = canonical(edge['action'])
            if action in recorded:
                fail('repeated certificate action')
            recorded[action] = canonical(nodes[destination]['state'])
        if actual != recorded:
            fail('action set or successor mismatch')
    reached = {0}
    queue = deque([0])
    while queue:
        for edge in nodes[queue.popleft()]['edges']:
            if edge['to'] not in reached:
                reached.add(edge['to'])
                queue.append(edge['to'])
    if len(reached) != len(nodes):
        fail('unreachable injected nodes')
    claimed = checked_value(certificate.get('best_value'))
    if claimed != best_value:
        fail('best value mismatch')
    best = certificate.get('best_terminal')
    if best_value is None:
        if best is not None:
            fail('best terminal in terminal-free graph')
    elif type(best) is not int or terminal_values.get(best) != best_value:
        fail('best terminal mismatch')
    return {'verified': True, 'scope': 'closed reachable graph in supplied model',
            'states_checked': len(nodes), 'terminal_states_checked': len(terminal_values),
            'best_value': list(best_value) if best_value is not None else None,
            'game_equivalence_verified': False}


def replay_trace(model: Model, actions: list[dict[str, Any]]) -> dict[str, Any]:
    state = clone(model.initial())
    states = [clone(state)]
    for index, action in enumerate(actions):
        if model.terminal_value(state) is not None:
            raise ContractError(f'action {index} follows a terminal state')
        matches = [edge for edge in model.transitions(state) if canonical(edge.action) == canonical(action)]
        if len(matches) != 1:
            raise ContractError(f'action {index} is not uniquely legal')
        state = clone(matches[0].state)
        states.append(state)
    value = checked_value(model.terminal_value(state))
    return {'states': states, 'terminal_value': list(value) if value is not None else None}
