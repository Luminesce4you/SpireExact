"""Named tactical plans for gate fights: plain request data for the native advisor.

A plan lists upstream search configurations ("members") that the host runs
from the same live fight root; it deploys the best forecast. Members are still
heuristic beam searches: a plan that loses proves nothing about the fight.

Evidence for the default plan (2026-10-01, seed 10101010, 98 retained boss
entries x 12 members, forecast equal to the native outcome in 1175 / 1176
runs): beam width is not monotone, so the union of a few widths rescues fights
that any single width loses (8 / 24 fatal act-1 boss entries, 2 / 24 act-2,
5 / 39 final-boss), and choosing the best winner raises the HP carried into
the second final boss. Escalating only when the first member already removed
half of the enemy HP kept 13 of 15 rescues.

Bosses of the last act (2026-10-02, three DEV seeds, 36 retained entries of the
first final boss): that escalation rule stopped after the first member on every
one of 11 entries a wider plan passes, and 10 of them are passed by members of
the default plan itself. The 'final' plans are sent under the request key
'FinalBoss', which the host prefers for a boss fight of the last act; they are
off unless --final-gate-plan names one.
"""
from copy import deepcopy

NODES = 120_000


def member(beam, mode='Evaluate', **extra):
    return {'mode': mode, 'beam': beam, 'nodes': NODES, **extra}


def _all_rooms(members, select='auto'):
    plan = {'members': members, 'select': select}
    return {'gate_plans': {room: deepcopy(plan) for room in ('Boss', 'Elite', 'Monster')}}


PRESETS = {
    # Historical behaviour: one member with the request's own beam and nodes.
    'none': {'plans': {}, 'retries': ()},
    'escalate': {
        'plans': {
            # escalate_percent: wider members run only when the first one left at
            # most this share of the enemy HP (integer: request identity has no floats).
            'Boss': {'members': [member(45), member(68), member(135), member(270)], 'select': 'auto', 'escalate_percent': 50},
            'Elite': {'members': [member(45), member(68), member(135)], 'select': 'first_win', 'escalate_percent': 50},
        },
        # Macro-level retries of a fatal fight entry (repair_mode 'gate'):
        # members the in-rollout plan did not try, cheapest level first.
        'retries': (
            _all_rooms([member(90), member(203), member(45, 'Coordinator', portfolio=True)]),
            _all_rooms([member(405), member(135, 'Coordinator', portfolio=True)]),
        ),
        # Bosses of the last act (--final-gate-plan): 'open' runs every member of the
        # boss plan whatever the first one forecasts.
        'final': {'open': {'members': [member(45), member(68), member(135), member(270)], 'select': 'auto'}},
        # Lighter plan for synthetic probe fights (--gate-probe-plan light): the first boss member only.
        'probe': {'gate_plans': {'Boss': {'members': [member(45)], 'select': 'auto'}}},
    },
}


def preset(name):
    if name not in PRESETS:
        raise ValueError('unknown gate preset: ' + str(name))
    return deepcopy(PRESETS[name])
