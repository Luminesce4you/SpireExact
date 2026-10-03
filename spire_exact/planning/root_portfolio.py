"""Diversify fresh starts using only the current native initial menu.

This is a capped heuristic allocation, not a legal-action exhaustiveness claim.
No seed lookup, prior trace, reconstructed effects, or projected-state merging.
"""
from copy import deepcopy
from ..canonical import canonical, ContractError

def root_requests(state, evaluator, limit):
    if state.get('status') != 'DECISION': return [], 0
    if state.get('trace'): raise ContractError('root portfolio requires an actual fresh initial menu')
    actions=[];seen=set()
    for action in state.get('actions', []):
        key=canonical(action)
        if key in seen: continue
        seen.add(key);actions.append(action)
    specs=[]
    for index, action in enumerate(actions[:limit]):
        specs.append({'kind':'root_rollout','category':'exploration',
                      'root_option_index':index,
                      'request':evaluator.request([deepcopy(action)])})
    return specs,max(0,len(actions)-limit)
