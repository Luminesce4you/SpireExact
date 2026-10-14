"""P1: F2 reroll, planner side (iteration-106).

When a trajectory loses F2 (the second boss of the last act), the host had
often already forecast other ways to win F1: at the first search of F1 every
member of the boss plan reports a forecast, and the host deploys only the
best one (iteration-064: 76% of the F1 entries before a lost F2 had another
winning forecast). F2's draws depend on how F1 was played (the shuffle stream
is shared by the whole run), so another F1 winner is another F2 attempt.

P1 re-solves F1 from its entry with a one-member plan for F1 only, for up to
`k` alternative winners: forecast to win, not selected, best first in the
host's own order (Forecast.Key, ties to the earlier member), skipping a
forecast equal to one already selected or queued (most likely the same line).
The rest of the run, F2 included, is played normally, so the reroll carries
the full native state (RNG and hooks) of its own F1, not a patched HP.

Allocation only: a reroll that loses proves nothing, and nothing is excluded.
A reroll is ordered by the source trajectory's utility, like gate retries, and
its source sits before the strategic scheduler (with an optional share).

Host requirement (not in frozen-i054a-shm): the advisor must honour the
gate-plan keys 'FinalBoss1' / 'FinalBoss2' (first / second boss of the last
act) before 'FinalBoss'. The current host parses unknown plan names and
ignores them, which would silently re-run the normal plan; so P1 refuses to
start unless the host declares the capability below in its build stamp.
Whether one member run alone reproduces its recorded forecast exactly
(session and continuation caching) is for the host side to check (106 F0-1,
F0-2).
"""
from __future__ import annotations
import hashlib
from copy import deepcopy

from ...canonical import canonical, ContractError
from ..archive import utility
from ..gatemodel import boss_fight_rows
from . import Plugin

CAPABILITY = 'gate_plans.final_boss_ordinal'
KINDS = ('f2_reroll',)


def _key(forecast):
    """BeamAdvisor Forecast.Key(): larger is better."""
    won = bool(forecast.get('won'))
    death = forecast.get('death_turn')
    return (int(won), int(bool(forecast.get('survives'))), int(forecast.get('hp') or 0) if won else 0,
            -int(forecast.get('potions') or 0) if won else 0, 0 if won else int(forecast.get('enemy_deaths') or 0),
            0 if won else (2_147_483_647 if death is None else int(death)), -int(forecast.get('enemy_hp') or 0))


def boss_search_groups(result):
    """The advisor's search records of boss fights, one list per fight in play
    order: a maximal run of consecutive boss records with one encounter (the
    two bosses of the last act are different encounters, and other bosses are
    separated by ordinary fights)."""
    groups, previous = [], None
    for record in (result.get('advisor_metrics') or {}).get('searches') or []:
        if record.get('room') != 'Boss':
            previous = None
            continue
        if previous is None or record.get('encounter') != previous:
            groups.append([])
        groups[-1].append(record)
        previous = record.get('encounter')
    return groups


def first_call(group):
    """Records of the first search call of one fight (member_index 0, 1, ...)."""
    if not group or group[0].get('member_index') != 0:
        return []
    call = [group[0]]
    for record in group[1:]:
        if int(record.get('member_index') or 0) <= int(call[-1].get('member_index') or 0):
            break
        call.append(record)
    return call


def f1_alternatives(result, history_length, limit):
    """(F1 row of boss_fight_rows, alternative member records) of a result that
    lost F2 after playing F1 itself; (None, []) otherwise. A fight inside the
    request's history was replayed, not searched, and has no records."""
    rows = boss_fight_rows(result)
    lost = next((row for row in rows if row['lost'] is not None), None)
    if lost is None or lost['gate'][1] != 1:
        return None, []
    f1 = next((row for row in rows if row['gate'] == (lost['gate'][0], 0)), None)
    if f1 is None or f1['index'] < history_length:
        return None, []
    played = [row for row in rows if row['index'] >= history_length]
    groups = boss_search_groups(result)
    if len(groups) != len(played):
        return None, []          # searches cannot be matched to fights: propose nothing
    call = first_call(groups[played.index(f1)])
    taken = [canonical(record.get('forecast') or {}) for record in call if record.get('selected')]
    ranked = sorted((record for record in call if not record.get('selected') and (record.get('forecast') or {}).get('won')),
                    key=lambda record: (tuple(-v for v in _key(record['forecast'])), int(record.get('member_index') or 0)))
    alternatives = []
    for record in ranked:
        forecast = canonical(record['forecast'])
        if forecast in taken:
            continue
        taken.append(forecast)
        alternatives.append(record)
        if len(alternatives) == limit:
            break
    return f1, alternatives


def last_act_boss_plan(advisor):
    """The boss plan the host used for F1 under `advisor`: 'FinalBoss', else 'Boss'."""
    plans = (advisor or {}).get('gate_plans') or {}
    return plans.get('FinalBoss') or plans.get('Boss')


def same_member(planned, recorded):
    return all(planned.get(key) == (recorded or {}).get(key) for key in ('mode', 'beam', 'nodes'))


class F2Reroll(Plugin):
    name = 'p1-f2-reroll'
    # k: alternative F1 winners re-solved per lost F2; share: percentage of all
    # scheduled evaluations the rerolls may hold (0 = no share limit).
    PARAMS = {'k': 3, 'share': 0}

    def __init__(self, **params):
        super().__init__(**params)
        if not 1 <= self.params['k'] <= 8 or not 0 <= self.params['share'] <= 100:
            raise ValueError(self.name + ': k must be within 1..8 and share within 0..100')
        self.queue = []
        self.seen = set()
        self.counts = {'f2_losses_seen': 0, 'without_alternative': 0, 'proposed': 0, 'duplicates': 0, 'dispatched': 0}

    def attach(self, s):
        stamp = getattr(s.pool, 'stamp', None) or {}
        if CAPABILITY not in (stamp.get('capabilities') or ()):
            raise ContractError(self.name + ': the native host does not declare ' + CAPABILITY +
                                ' (FinalBoss1 / FinalBoss2 gate plans); refusing to run')
        if s.ev.advisor is None or not last_act_boss_plan(s.ev.advisor):
            raise ContractError(self.name + ': needs the combat advisor with a boss gate plan')
        # A component's door is a scope too; re-rolls start at an F1 entry the
        # request played, so never before the door (f1_alternatives).
        if s.scope_prefix is not None and getattr(s, 'component', None) is None:
            raise ContractError(self.name + ': needs an unscoped search or component mode')

    def observe(self, s, spec, candidate, label, classification, family):
        if classification != 'NATIVE_ROUTE_DEATH':
            return
        request = spec['request']
        f1, alternatives = f1_alternatives(candidate, len(request['history']), self.params['k'])
        if f1 is None:
            return
        self.counts['f2_losses_seen'] += 1
        if not alternatives:
            self.counts['without_alternative'] += 1
            return
        plan = last_act_boss_plan(request.get('advisor'))
        floor = f1['entry'].get('floor')
        if not plan or floor is None:
            return
        cfg = s.config
        prefix = candidate['trace'][:f1['index']]
        for rank, record in enumerate(alternatives):
            j = int(record['member_index'])
            if j >= len(plan['members']) or not same_member(plan['members'][j], record.get('member')):
                continue
            member = deepcopy(plan['members'][j])
            key = hashlib.sha256(canonical({'prefix': prefix, 'member': member})).hexdigest()
            if key in self.seen:
                self.counts['duplicates'] += 1
                continue
            self.seen.add(key)
            base = deepcopy((s.ev.advisor or {}).get('gate_plans') or {})
            patch = {'gate_plans': {**base, 'FinalBoss1': {'members': [member], 'select': 'first_win'}}}
            self.queue.append({'kind': 'f2_reroll', 'category': 'failure', 'priority': utility(candidate), 'family': family,
                'repair': {'source': label, 'floor': floor, 'room': 'Boss', 'member': j, 'rank': rank},
                'request': s.ev.request(prefix, policy=0, horizon=floor + cfg.lookahead_floors,
                    actions=min(cfg.max_decisions, len(prefix) + cfg.lookahead_actions), advisor_patch=patch)})
            self.counts['proposed'] += 1

    def pop(self, slot):
        if not self.queue:
            return None
        # Best source trajectory first; equal priorities keep arrival order (RepairQueue 'gate').
        index = max(range(len(self.queue)), key=lambda i: (self.queue[i].get('priority', ()), -i))
        self.counts['dispatched'] += 1
        return self.queue.pop(index)

    def sources(self, s):
        from ..core import Source
        share = self.params['share']
        return [Source(self.name, self.pop, pending=lambda: bool(self.queue), share=share / 100 if share else None, kinds=KINDS)]

    def metrics(self, s):
        return {**self.counts, 'pending': len(self.queue)}

    def reaches(self, result):
        """A run reached P1 when it lost F2 at least once: the second boss of
        the last act has an entry besides a single passed one."""
        snapshot = result.get('gate_models')
        if not snapshot:
            return True
        second = [gate for gate in snapshot.get('gates', []) if gate.get('ordinal') == 1]
        return any(gate.get('entries', 0) > 1 or gate.get('survived_entries', 0) < gate.get('entries', 0) for gate in second)
