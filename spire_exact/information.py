"""Exact finite-world policies with explicit information boundaries.

A prior is an input, not inferred from a known seed. Worlds with identical
observations share an action. Values use rational arithmetic; cycles, missing
rules and budgets are incomplete, never losses or optimal policies.
"""
from collections import deque
from fractions import Fraction
from time import perf_counter
from .canonical import ContractError, canonical, clone, digest
from .core import Limits


def rational(value):
    if type(value) is int:
        return Fraction(value)
    if type(value) is list and len(value) == 2 and all(type(n) is int for n in value):
        if value[1] <= 0:
            raise ContractError('probability denominator must be positive')
        return Fraction(*value)
    raise ContractError('probabilities must be integers or [numerator, denominator]')


def encoded(value):
    return [value.numerator, value.denominator]


def solve_information(spec, mode='hidden', world=None, limits=Limits()):
    spec = clone(spec)
    if spec.get('schema') != 'spire-information-model/v1':
        raise ContractError('expected spire-information-model/v1')
    if mode not in ('hidden', 'omniscient'):
        raise ContractError('mode must be hidden or omniscient')
    worlds = spec.get('worlds')
    if type(worlds) is not list or not worlds:
        raise ContractError('provide a finite, explicit prior over worlds')
    identifiers = [w['id'] for w in worlds]
    if len(set(identifiers)) != len(identifiers):
        raise ContractError('duplicate world id')
    weights = [rational(w['probability']) for w in worlds]
    if any(p <= 0 for p in weights) or sum(weights) != 1:
        raise ContractError('world probabilities must be positive and sum exactly to one')
    if mode == 'omniscient':
        if world not in identifiers:
            raise ContractError('omniscient mode requires an explicit world id')
        initial = [(identifiers.index(world), worlds[identifiers.index(world)]['root'], Fraction(1))]
    else:
        initial = [(i, w['root'], weights[i]) for i, w in enumerate(worlds)]

    start = perf_counter()
    nodes, index, pending = [], {}, deque()
    stop = None
    edge_count = 0
    blockers = []
    expansions = 0

    def state(i, key):
        try:
            s = worlds[i]['states'][key]
        except KeyError as error:
            raise ContractError('missing world state') from error
        if 'observation' not in s:
            raise ContractError('every state needs an explicit visible observation')
        return s

    def observation_key(i, key):
        s = state(i, key)
        # Terminal outcome is observable; score must be included in observation by the model.
        obs = s['observation']
        return canonical([i, key, obs] if mode == 'omniscient' else obs)

    def register(belief):
        nonlocal stop
        belief = sorted(belief)
        key = canonical([[i, s, encoded(p)] for i, s, p in belief])
        if key in index:
            return index[key]
        if len(nodes) >= limits.max_states:
            stop = 'state_budget'
            return None
        terminal_flags = ['terminal' in state(i, s) for i, s, _ in belief]
        if any(terminal_flags) and not all(terminal_flags):
            raise ContractError('same observation hides whether the run has ended')
        value = None
        if all(terminal_flags):
            value = (Fraction(0), Fraction(0))
            terminal_observations = set()
            for i, s, p in belief:
                t = state(i, s)['terminal']
                if type(t.get('victory')) is not bool or type(t.get('score')) is not int:
                    raise ContractError('terminal requires boolean victory and integer native/model score')
                terminal_observations.add((t['victory'], t['score']))
                value = (value[0] + p * int(t['victory']), value[1] + p * t['score'])
            if len(terminal_observations) != 1:
                raise ContractError('terminal outcome/score must be visible in the observation')
        node_id = len(nodes)
        index[key] = node_id
        nodes.append({'belief': belief, 'actions': [], 'complete': value is not None,
                      'value': value, 'best': None,
                      'observation': clone(state(belief[0][0], belief[0][1])['observation'])})
        if value is None:
            pending.append(node_id)
        return node_id

    def branches(members):
        grouped = {}
        for i, s, p in members:
            grouped.setdefault(observation_key(i, s), []).append((i, s, p))
        result = []
        for _, group in sorted(grouped.items()):
            probability = sum(p for _, _, p in group)
            target = register([(i, s, p / probability) for i, s, p in group])
            if target is None:
                return None
            result.append((probability, target))
        return result

    roots = branches(initial)
    while pending and stop is None:
        if limits.max_seconds is not None and perf_counter() - start >= limits.max_seconds:
            stop = 'time_budget'
            break
        if expansions >= limits.max_expansions:
            stop = 'expansion_budget'
            break
        node_id = pending.popleft()
        node = nodes[node_id]
        expansions += 1
        legal, transitions = None, []
        unsupported = False
        for i, s, p in node['belief']:
            native = state(i, s)
            if native.get('complete', True) is not True:
                blockers.append({'node': node_id, 'reason': native.get('reason', 'incomplete actions')})
                unsupported = True
                break
            choices = {}
            for edge in native.get('actions', []):
                if type(edge['action']) is not dict:
                    raise ContractError('action must be an object')
                key = canonical(edge['action'])
                if key in choices:
                    raise ContractError('duplicate deterministic action')
                choices[key] = edge
            if legal is None:
                legal = set(choices)
            elif legal != set(choices):
                raise ContractError('identical visible observations must expose identical legal action menus')
            transitions.append((i, p, choices))
        if unsupported:
            continue
        if not legal:
            blockers.append({'node': node_id, 'reason': 'nonterminal state has no actions'})
            continue
        for key in sorted(legal):
            if limits.max_seconds is not None and perf_counter() - start >= limits.max_seconds:
                stop = 'time_budget'
                break
            if edge_count >= limits.max_edges:
                stop = 'edge_budget'
                break
            outcomes = branches([(i, choices[key]['to'], p) for i, p, choices in transitions])
            if outcomes is None:
                break
            if edge_count + len(outcomes) > limits.max_edges:
                stop = 'edge_budget'
                break
            edge_count += len(outcomes)
            node['actions'].append({'action': clone(transitions[0][2][key]['action']), 'outcomes': outcomes})
        node['complete'] = stop is None

    # Backward evaluation of the complete acyclic part, using distinct dependencies.
    parents = [set() for _ in nodes]
    dependencies = []
    for n, node in enumerate(nodes):
        children = {target for a in node['actions'] for _, target in a['outcomes']}
        dependencies.append(children)
        for child in children:
            parents[child].add(n)
    ready = deque(i for i, n in enumerate(nodes) if n['value'] is not None)
    while ready:
        child = ready.popleft()
        for parent in parents[child]:
            dependencies[parent].discard(child)
            node = nodes[parent]
            if dependencies[parent] or not node['complete'] or not node['actions']:
                continue
            values = []
            for action in node['actions']:
                v = tuple(sum(p * nodes[target]['value'][k] for p, target in action['outcomes']) for k in (0, 1))
                action['value'] = v
                values.append(v)
            node['best'] = max(range(len(values)), key=values.__getitem__)
            node['value'] = values[node['best']]
            ready.append(parent)
    unresolved = [i for i, n in enumerate(nodes) if n['value'] is None]
    complete = roots is not None and not unresolved and stop is None and not blockers
    value = (tuple(sum(p * nodes[t]['value'][k] for p, t in roots) for k in (0, 1))
             if complete else None)
    policy = []
    if complete:
        reachable, queue = set(), deque(t for _, t in roots)
        while queue:
            i = queue.popleft()
            if i in reachable:
                continue
            reachable.add(i)
            node = nodes[i]
            if node['best'] is None:
                continue
            action = node['actions'][node['best']]
            policy.append({'node': i, 'observation': node['observation'], 'action': action['action'],
                           'win_probability': encoded(node['value'][0]),
                           'expected_score': encoded(node['value'][1]),
                           'next_observations': [{'probability': encoded(p), 'node': t,
                                                 'observation': nodes[t]['observation']}
                                                for p, t in action['outcomes']]})
            queue.extend(t for _, t in action['outcomes'])
    return {
        'schema': 'spire-information-result/v1', 'mode': mode, 'world': world if mode == 'omniscient' else None,
        'model_sha256': digest(spec), 'status': 'OPTIMAL_IN_MODEL' if complete else 'INCOMPLETE',
        'proven_optimal_in_model': complete, 'game_equivalence_verified': False,
        'objective': ['maximize probability of complete-run victory', 'maximize expected final score'],
        'probability_scope': 'exact only under the explicit supplied finite prior; not an empirical STS2 win rate',
        'win_probability': encoded(value[0]) if value else None,
        'expected_score': encoded(value[1]) if value else None,
        'guaranteed_victory': value[0] == 1 if value else None,
        'policy': policy,
        'initial_observations': [{'probability': encoded(p), 'node': t, 'observation': nodes[t]['observation']}
                                 for p, t in (roots or [])],
        'stop_reason': stop or ('unsupported_or_cyclic_graph' if unresolved else None),
        'unsupported': blockers, 'unresolved_nodes': unresolved,
        'statistics': {'belief_states': len(nodes), 'expansions': expansions, 'edges': edge_count}
    }
