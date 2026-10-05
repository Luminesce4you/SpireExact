"""Synthetic campaign worlds that drive the real planner loop offline.

NOT the game. A seeded, three-act deck-building campaign with a double final
boss whose hidden structure is controlled per world class, exposed to the
planner only through the same request/result protocol the native host uses
(trace, decision_evidence, observation with a name-free `strategic` snapshot,
terminal_combat, probe decisions, performance). A fake worker pool with a
simulated clock runs `spire_exact.planning.search.solve` unmodified, so the
real dispatch, absorption, focus/clinic scheduling, gate retries, readiness
probes and paired tables are exercised.

Everything here is fabricated. Simulated seconds are a cost model, not native
time; a simulated win is not a game win. Isolated research consumers that need
native artifacts (F1-winner plans, route graphs, forge menus, completed-prefix
journals) stay inert because the simulator emits no such artifacts.
"""
from __future__ import annotations

from concurrent.futures import Future
import concurrent.futures
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import math
import random
from types import SimpleNamespace
from unittest import mock

from spire_exact.canonical import canonical
from spire_exact.mode1 import OBJECTIVE
from spire_exact.planning.policy_data import TIERS

SHA = '0' * 64
IDENTITY = {'host_sha256': SHA, 'game_sha256': SHA}
INPUTS = {'sources': {'SimHost.cs': SHA},
          'dependencies': {'sts2.dll': SHA, 'GodotSharp.dll': SHA, '0Harmony.dll': SHA},
          'game_data_dir': 'simulated-campaign', 'native_runtime': {'profile': 'simulated'}}
STEPS = 7
CLASSES = ('easy', 'tactical', 'resource', 'root_act2', 'root_act1', 'mixed', 'unsolvable')
MIX = (('easy', 16), ('tactical', 6), ('resource', 6), ('root_act2', 5), ('root_act1', 3), ('mixed', 2), ('unsolvable', 2))
REAL_NAMES = sorted({label[5:] for table in TIERS.values() for label in table if label.startswith('card:')})
SIM_VERSION = 'sim-campaign/v1'


def _h(*parts) -> int:
    return int.from_bytes(hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).digest()[:8], 'big')


def _rng(*parts) -> random.Random:
    return random.Random(_h(*parts))


def _gauss(*parts) -> float:
    rng = _rng(*parts)
    return rng.gauss(0.0, 1.0)


def floor_of(act, step):
    return 16 * act + 2 * step + 1


# ---------------------------------------------------------------- the world
class World:
    """Hidden parameters of one synthetic seed."""

    def __init__(self, seed: int, kind: str):
        if kind not in CLASSES:
            raise ValueError('unknown world class')
        self.seed, self.kind = int(seed), kind
        rng = _rng('world', seed, kind)
        names = list(REAL_NAMES)
        rng.shuffle(names)
        self.cards = {'STRIKE_IRONCLAD': {'kind': 'attack', 'dmg': 6.0, 'blk': 0.0, 'drw': 0.0, 'scl': 0.0, 'native_scl': 0.0},
                      'DEFEND_IRONCLAD': {'kind': 'skill', 'dmg': 0.0, 'blk': 5.0, 'drw': 0.0, 'scl': 0.0, 'native_scl': 0.0},
                      'BASH': {'kind': 'attack', 'dmg': 9.0, 'blk': 0.0, 'drw': 0.0, 'scl': 0.0, 'native_scl': 0.0},
                      'ASCENDERS_BANE': {'kind': 'curse', 'dmg': 0.0, 'blk': 0.0, 'drw': 0.0, 'scl': 0.0, 'native_scl': 0.0}}
        pool = names[:66]
        self.key_cards = []
        for index, name in enumerate(pool):
            roll = rng.random()
            if roll < 0.55:
                card = {'kind': 'attack', 'dmg': float(rng.randint(7, 14)), 'blk': 0.0,
                        'drw': 1.0 if rng.random() < 0.1 else 0.0, 'scl': 0.0}
            elif roll < 0.85:
                card = {'kind': 'skill', 'dmg': 0.0, 'blk': float(rng.randint(6, 12)),
                        'drw': float(rng.choice((0, 0, 1, 2))), 'scl': 0.0}
            else:
                card = {'kind': 'power', 'dmg': 0.0, 'blk': 0.0, 'drw': 0.0, 'scl': round(rng.uniform(0.4, 0.8), 2)}
            card['native_scl'] = card['scl']
            self.cards[name] = card
        # Key scaling sources: strong hidden scaling the native per-card score
        # undervalues; chosen among names neither community table promotes, so
        # no root family takes them by default.
        def promoted(name):
            return any(max(table.get('card:' + name, [0])) > 0 for table in TIERS.values())
        keys = [name for name in pool[48:] if not promoted(name)][:6]
        if len(keys) < 6:
            keys += [name for name in pool[48:] if name not in keys][:6 - len(keys)]
        for name in keys:
            self.cards[name] = {'kind': 'power', 'dmg': 0.0, 'blk': 0.0, 'drw': 0.0, 'scl': 3.0, 'native_scl': 0.4}
        self.key_cards = keys
        regular = [name for name in pool[:48]]
        powers = [n for n in regular if self.cards[n]['kind'] == 'power']
        plain = [n for n in regular if self.cards[n]['kind'] != 'power']
        acts = [plain[i::3] + powers[i:i + 1] for i in range(3)]
        # Where key scaling sources can be offered.
        key_acts = {'easy': (0, 1, 2), 'tactical': (0, 1, 2), 'resource': (0, 1, 2), 'root_act2': (1,),
                    'root_act1': (0,), 'mixed': (1,), 'unsolvable': ()}[kind]
        for act in key_acts:
            acts[act] = acts[act] + keys[:3] if kind not in ('easy', 'tactical', 'resource') else acts[act] + keys[act * 2:act * 2 + 2]
        if kind not in ('easy', 'tactical', 'resource'):
            # Final act offers no strong scaling: a deck built before act 3 decides F2.
            acts[2] = [n for n in acts[2] if n not in keys and self.cards[n]['kind'] != 'power']
        self.pools = acts
        self.key_weight = {'easy': 1.0, 'tactical': 1.0, 'resource': 1.0, 'root_act2': 1.6, 'root_act1': 1.6,
                           'mixed': 1.6, 'unsolvable': 0.0}[kind]
        # Enemies: (HP, base attack, multiplicative growth per turn, tactical headroom).
        f2_growth = {'easy': 0.07, 'tactical': 0.07, 'resource': 0.03, 'root_act2': 0.15, 'root_act1': 0.15,
                     'mixed': 0.13, 'unsolvable': 0.30}[kind]
        f2_hp = {'root_act2': 400.0, 'root_act1': 400.0, 'mixed': 380.0}.get(kind, 330.0)
        f2_attack = {'easy': 17.0, 'tactical': 18.0, 'resource': 25.0, 'root_act2': 16.0, 'root_act1': 16.0,
                     'mixed': 20.0, 'unsolvable': 34.0}[kind]
        tactical = 0.13 if kind == 'tactical' else 0.035
        F1_ATTACK = 17.0
        jitter = 1.0 + 0.08 * rng.uniform(-1, 1)
        self.enemies = {
            'Monster': [(30.0, 6.0, 0.02, 0.03), (44.0, 9.0, 0.02, 0.03), (58.0, 12.0, 0.02, 0.03)],
            'Elite': [(70.0, 10.0, 0.04, 0.05), (100.0, 13.0, 0.04, 0.05), (130.0, 16.0, 0.04, 0.05)],
            'Boss': [(150.0, 11.0, 0.04, 0.05), (230.0, 15.0, 0.05, 0.05)],
            'F1': (300.0 * jitter, F1_ATTACK, 0.05, tactical),
            'F2': (f2_hp * jitter, f2_attack, f2_growth, tactical),
        }

    def offers(self, act, step, room, path, count=3):
        rng = _rng('offer', self.seed, act, step, room, path)
        pool = list(self.pools[act])
        weights = [self.key_weight if name in self.key_cards else 1.0 for name in pool]
        chosen = []
        while pool and len(chosen) < count:
            total = sum(weights)
            if total <= 0:
                break
            pick = rng.random() * total
            for index, weight in enumerate(weights):
                pick -= weight
                if pick <= 0:
                    break
            chosen.append(pool.pop(index))
            weights.pop(index)
        return chosen

    def rooms(self, act, step, path):
        rng = _rng('map', self.seed, act, step, path)
        types = ['Monster', 'Monster', 'Elite', 'RestSite', 'Shop', 'Event']
        if act == 2 and self.kind in ('resource', 'mixed'):
            types += ['RestSite', 'Elite']
        count = 2 if rng.random() < 0.55 else 3
        rooms = []
        while len(rooms) < count:
            room = rng.choice(types)
            if room not in rooms:
                rooms.append(room)
        return rooms

    def describe(self):
        return {'seed': self.seed, 'kind': self.kind, 'key_cards': list(self.key_cards),
                'f2': list(self.enemies['F2']), 'f1': list(self.enemies['F1'])}


# ---------------------------------------------------------------- state
def card_value(world, card_id, upgraded):
    card = world.cards[card_id]
    factor = 1.35 if upgraded else 1.0
    return card, card['dmg'] * factor, card['blk'] * factor, card['drw'], card['scl'] + (0.5 if upgraded and card['scl'] else 0.0)


def strategic(world, deck):
    n = max(1, len(deck))
    damage = block = draw = scaling_count = strength = burden = upgraded = attacks = defends = 0.0
    for cid, upg in deck:
        card, dmg, blk, drw, scl = card_value(world, cid, upg)
        damage += dmg
        block += blk
        draw += drw
        upgraded += bool(upg)
        attacks += dmg > 0
        defends += blk > 0
        burden += card['kind'] == 'curse'
        if card['kind'] == 'power':
            scaling_count += 1
            if scl >= 2.0:
                strength += 1
    fmt = lambda x: repr(float(round(x, 6)))
    return {'schema': 'strategic-capability/v1', 'deck_size': len(deck), 'attack_density': fmt(attacks / n),
            'defense_density': fmt(defends / n), 'damage_per_draw': fmt(damage / n), 'block_per_draw': fmt(block / n),
            'draw_per_card': fmt(draw / n), 'energy_per_card': fmt(0.0), 'mean_energy_cost': fmt(1.0),
            'strength': fmt(strength), 'scaling': fmt(scaling_count), 'aoe': fmt(0.0), 'weak': fmt(0.0),
            'vulnerable': fmt(1.0), 'exhaust': fmt(0.0), 'exhaust_payoff': fmt(0.0), 'burden': int(burden),
            'self_exhaust': 0, 'steady_block_per_draw': fmt(block / n), 'deck_potential': fmt(damage / n + block / n),
            'upgrade_density': fmt(upgraded / n), 'score_is_heuristic': True}


class State:
    def __init__(self):
        self.act, self.step, self.hp, self.max_hp, self.gold = 0, 0, 80, 80, 99
        self.deck = [('STRIKE_IRONCLAD', 0)] * 5 + [('DEFEND_IRONCLAD', 0)] * 4 + [('BASH', 0), ('ASCENDERS_BANE', 0)]
        self.relics = ['BURNING_BLOOD']
        self.path = 'root'
        self.floor = 0

    def key(self):
        return _h(self.act, self.step, self.hp, self.max_hp, self.gold, sorted(self.deck), self.relics, self.path)

    def observation(self, world, room=None, turn=None, enemies=None):
        return {'act': self.act, 'floor': self.floor, 'room': room, 'hp': str(int(max(0, self.hp))),
                'max_hp': str(int(self.max_hp)), 'gold': int(self.gold),
                'deck': [{'id': cid, 'upgrade': int(upg)} for cid, upg in self.deck],
                'relics': list(self.relics), 'strategic': strategic(world, self.deck), 'selection': None,
                'hand': None, 'turn': turn, 'energy': None, 'block': '0', 'enemies': enemies,
                'potions': [None, None], 'character': 'IRONCLAD', 'rng': {'sim_path': self.path}, 'score': 0}


# ---------------------------------------------------------------- combat
def members_of(plan):
    members = []
    for member in (plan or {}).get('members') or []:
        beam = int(member.get('beam') or 45)
        level = math.log2(max(1.0, beam) / 45.0)
        members.append({'mode': member.get('mode', 'Evaluate'), 'beam': beam, 'level': level,
                        'nodes': int(member.get('nodes') or 120000)})
    return members


def plan_for(advisor, room, final_act_boss):
    plans = (advisor or {}).get('gate_plans') or {}
    if final_act_boss and 'FinalBoss' in plans:
        return plans['FinalBoss'], 'FinalBoss'
    if room in plans:
        return plans[room], room
    return None, None


class Fight:
    """Deterministic fight of the current deck against one enemy."""

    def __init__(self, world, state, enemy, fight_id, cls):
        self.world, self.state, self.enemy, self.fight_id, self.cls = world, state, enemy, fight_id, cls

    def member(self, level, sample):
        hp_enemy, attack, growth, headroom = self.enemy
        deck = self.state.deck
        n = max(1, len(deck))
        dmg = blk = drw = scaling = 0.0
        for cid, upg in deck:
            _, d, b, w, s = card_value(self.world, cid, upg)
            dmg += d
            blk += b
            drw += w
            scaling += s
        hand = min(10.0, 5.0 + 5.0 * drw / n)
        plays = min(hand, 4.5)
        consistency = min(1.0, (22.0 / n) ** 0.35)
        relics = 1.0 + 0.04 * (len(self.state.relics) - 1)
        efficiency = 0.82 * (1.0 + headroom * level) * math.exp(0.07 * _gauss('fight', self.world.seed, self.state.key(),
                                                                              self.fight_id, round(level, 3), sample))
        base_damage = plays * dmg / n * consistency * relics * efficiency
        base_block = plays * blk / n * consistency * relics * efficiency
        enemy_hp, hp = hp_enemy, float(self.state.hp)
        turn = 0
        for turn in range(1, 41):
            enemy_hp -= base_damage + 2.6 * scaling * (turn - 1) * efficiency
            if enemy_hp <= 0:
                return {'won': True, 'hp': max(1.0, hp), 'turns': turn, 'removed': hp_enemy, 'total': hp_enemy}
            hit = attack * (1.0 + growth) ** (turn - 1)
            hp -= max(0.0, hit - base_block)
            if hp <= 0:
                return {'won': False, 'hp': 0.0, 'turns': turn, 'removed': hp_enemy - max(0.0, enemy_hp), 'total': hp_enemy}
        return {'won': False, 'hp': 0.0, 'turns': turn, 'removed': hp_enemy - max(0.0, enemy_hp), 'total': hp_enemy}

    @staticmethod
    def plan_members(plan):
        return members_of(plan) or [{'mode': 'Evaluate', 'beam': 45, 'level': -1.0, 'nodes': 10000}]

    def run_member(self, plan, index, sample=0):
        # Every member is an independent sample at its beam level; no mode
        # (Evaluate or Coordinator) is assumed stronger than another.
        member = self.plan_members(plan)[index]
        level = member['level']
        outcome = self.member(level, (sample, index, member['mode']))
        outcome['member'] = index
        return outcome

    def solve(self, plan, select, room, prefer_hp=False, escalate=None, final_second=False, sample=0):
        members = self.plan_members(plan)
        cost, outcomes = 0.0, []
        unit = {'Monster': 0.45, 'Elite': 1.4, 'Boss': 3.0, 'F1': 4.0, 'F2': 4.0}[self.cls]
        for index, member in enumerate(members):
            outcome = self.run_member(plan, index, sample)
            cost += 0.45 if plan is None else unit * (1.0 + 0.22 * max(0.0, member['level']))
            outcomes.append(outcome)
            if index == 0 and escalate is not None and not outcome['won'] and outcome['removed'] < escalate / 100.0 * outcome['total']:
                break
            if outcome['won'] and select in ('first_win',) :
                break
            if outcome['won'] and select == 'auto' and (final_second or not prefer_hp):
                break
        wins = [o for o in outcomes if o['won']]
        if wins:
            chosen = max(wins, key=lambda o: (o['hp'], -o['member'])) if (select in ('best', 'auto') and prefer_hp) else wins[0]
        else:
            chosen = max(outcomes, key=lambda o: (o['removed'], -o['member']))
        return chosen, cost, len(outcomes)


# ---------------------------------------------------------------- rollout
class Engine:
    def __init__(self, world, log=None):
        self.world = world
        self.log = log

    # Native per-option score (myopic hand-written heuristic).
    def card_score(self, state, card_id):
        card = self.world.cards[card_id]
        deck = state.deck
        n = max(1, len(deck))
        attacks = sum(self.world.cards[c]['dmg'] > 0 for c, _ in deck) / n
        defends = sum(self.world.cards[c]['blk'] > 0 for c, _ in deck) / n
        score = card['dmg'] * (1.0 + max(0.0, 0.4 - attacks) * 2) + card['blk'] * (0.9 + max(0.0, 0.3 - defends) * 2 + 0.08 * state.act)
        score += card['drw'] * 4.0 + card['native_scl'] * (3.0 + state.act)
        score -= max(0, n - 17) * 0.32
        return score

    @staticmethod
    def tier(prior, labels, act):
        if not prior or not labels:
            return 0
        best = None
        for label in labels:
            row = prior.get(label)
            if row:
                value = row[min(act, len(row) - 1)]
                best = value if best is None else max(best, value)
        return best or 0

    def choose(self, request, state, actions, labels, scores):
        prior = request.get('policy_prior') or {}
        seed = request.get('policy_seed') or 0
        best = None
        for index, (action, names) in enumerate(zip(actions, labels)):
            jitter = (_h('policy', seed, canonical(action).decode(), state.key()) % 1000) / 1e6
            key = (self.tier(prior, names or (), state.act), scores[index] + jitter, -index)
            if best is None or key > best[0]:
                best = (key, index)
        return best[1]

    def menu(self, state, phase, rooms=None, offers=None):
        """(actions, labels, scores, effects) of one strategic decision."""
        hp_frac = state.hp / state.max_hp
        if phase == 'map':
            actions = [{'kind': 'map', 'col': index, 'row': state.step, 'act': state.act} for index in range(len(rooms))]
            table = ({'Elite': 3.0, 'Monster': 2.0, 'Event': 1.5, 'Shop': 1.0, 'RestSite': 0.5} if hp_frac >= 0.65 else
                     {'RestSite': 3.0, 'Shop': 1.5, 'Event': 1.2, 'Monster': 1.0, 'Elite': 0.0} if hp_frac < 0.4 else
                     {'Monster': 2.0, 'Event': 1.6, 'RestSite': 1.4, 'Shop': 1.0, 'Elite': 1.0})
            scores = [table.get(room, 0.0) + (1.0 if room == 'Shop' and state.gold >= 150 else 0.0) for room in rooms]
            return actions, [None] * len(rooms), scores, [('room', room) for room in rooms]
        if phase == 'card_reward':
            actions = [{'kind': 'card_reward', 'index': i} for i in range(len(offers))] + [{'kind': 'card_skip'}]
            labels = [['card:' + name] for name in offers] + [['card_skip']]
            scores = [self.card_score(state, name) for name in offers] + [2.0]
            return actions, labels, scores, [('card', name) for name in offers] + [('skip', None)]
        if phase == 'rest':
            actions = [{'kind': 'rest', 'option': 'HEAL'}, {'kind': 'rest', 'option': 'SMITH'}]
            scores = [1.0, 2.0] if hp_frac >= 0.6 else [2.0, 1.0]
            return actions, [['rest'], ['smith']], scores, [('heal', None), ('smith', None)]
        if phase == 'shop':
            price = 70 + 5 * state.act
            actions, labels, scores, effects = [], [], [], []
            for index, name in enumerate(offers):
                if state.gold >= price:
                    actions.append({'kind': 'shop', 'buy': index})
                    labels.append(['card:' + name])
                    scores.append(self.card_score(state, name) - 4.0)
                    effects.append(('buy', name))
            if state.gold >= 100 and ('STRIKE_IRONCLAD', 0) in state.deck:
                actions.append({'kind': 'shop', 'remove': 'STRIKE_IRONCLAD'})
                labels.append(['buy_removal'])
                scores.append(5.0)
                effects.append(('remove', 'STRIKE_IRONCLAD'))
            actions.append({'kind': 'shop', 'leave': True})
            labels.append(['go'])
            scores.append(0.0)
            effects.append(('leave', None))
            return actions, labels, scores, effects
        if phase == 'event':
            name = offers[0]
            actions = [{'kind': 'event', 'option': 'card'}, {'kind': 'event', 'option': 'heal'},
                       {'kind': 'event', 'option': 'gold'}]
            card_score = self.card_score(state, name)
            scores = [card_score - 2.0 if hp_frac >= 0.5 else -5.0, 4.0 if hp_frac < 0.5 else 0.5, 1.0]
            return actions, [['card:' + name], ['event:heal'], ['event:gold']], scores, [('event_card', name), ('heal', 12), ('gold', 40)]
        raise ValueError(phase)

    def apply(self, state, effect):
        kind, value = effect
        if kind == 'card' or kind == 'event_card':
            state.deck = state.deck + [(value, 0)]
            if kind == 'event_card':
                state.hp = max(1, state.hp - 6)
        elif kind == 'buy':
            state.deck = state.deck + [(value, 0)]
            state.gold -= 70 + 5 * state.act
        elif kind == 'remove':
            deck = list(state.deck)
            deck.remove((value, 0))
            state.deck = deck
            state.gold -= 100
        elif kind == 'heal':
            amount = value if value is not None else 0.3 * state.max_hp
            state.hp = min(state.max_hp, state.hp + amount)
        elif kind == 'smith':
            candidates = [(self.card_score(state, cid), i) for i, (cid, upg) in enumerate(state.deck)
                          if not upg and self.world.cards[cid]['kind'] != 'curse']
            if candidates:
                _, index = max(candidates)
                deck = list(state.deck)
                deck[index] = (deck[index][0], 1)
                state.deck = deck
        elif kind == 'gold':
            state.gold += value

    def run(self, request, *, probe=None):
        """One evaluation: replay `history`, then continue with the native policy."""
        world, history = self.world, request.get('history') or []
        state = State()
        trace, evidence = [], []
        cost = 0.0
        searches = []
        status = None
        advisor = request.get('advisor') or {}
        prefer = bool(advisor.get('prefer_f1_hp'))
        horizon = request.get('stop_at_floor')
        limit = request.get('max_decisions') or 12000

        def decide(phase, rooms=None, offers=None, room=None):
            nonlocal cost
            index = len(trace)
            if index >= limit:
                raise _Stop()
            actions, labels, scores, effects = self.menu(state, phase, rooms, offers)
            obs = state.observation(world, room=room)
            if index < len(history):
                wanted = canonical(history[index])
                matches = [i for i, a in enumerate(actions) if canonical(a) == wanted]
                if not matches:
                    raise SimInvalid('history action is not legal at %d (%s)' % (index, phase))
                choice = matches[0]
            else:
                choice = self.choose(request, state, actions, labels, scores)
                cost += 0.02
            row = {'phase': phase, 'observation': obs, 'available_actions': actions}
            if any(labels):
                row['option_labels'] = [list(names) if names else ['none'] for names in labels]
            trace.append(actions[choice])
            evidence.append(row)
            state.path = hashlib.sha256((state.path + canonical(actions[choice]).decode()).encode()).hexdigest()[:16]
            self.apply(state, effects[choice])

        def fight(room, cls, enemy, fight_id, final_second=False):
            """Combat rows; returns True when the player survives."""
            nonlocal cost
            index = len(trace)
            if index >= limit:
                raise _Stop()
            final_boss = cls in ('F1', 'F2')
            engine = Fight(world, state, enemy, fight_id, cls)
            if index < len(history):
                recorded = history[index] or {}
                signature, member = recorded.get('plan'), recorded.get('member')
                if signature is None or type(member) is not int:
                    raise SimInvalid('combat history without plan signature')
                plan = json.loads(signature) if signature != 'normal' else None
                if not 0 <= member < len(Fight.plan_members(plan)):
                    raise SimInvalid('combat history member out of range')
                outcome = engine.run_member(plan, member)
            else:
                plan, _ = plan_for(advisor, 'Boss' if final_boss else room, final_boss and state.act == 2)
                signature = 'normal' if plan is None else canonical(plan).decode()
                select = (plan or {}).get('select', 'first_win')
                escalate = (plan or {}).get('escalate_percent')
                outcome, spent, members = engine.solve(plan, select, room, prefer_hp=prefer and cls == 'F1',
                                                       escalate=escalate, final_second=(cls == 'F2'))
                cost += spent
                searches.append({'expanded_nodes': 1000 * members, 'wall_us': int(spent * 1e6)})
                member = outcome['member']
            enemy_id = {'F1': 'FINAL_ONE', 'F2': 'FINAL_TWO', 'Boss': 'ACT_BOSS', 'Elite': 'ELITE', 'Monster': 'MONSTER'}[cls]
            total = enemy[0]
            start = state.observation(world, room=room, turn=1,
                                      enemies=[{'id': enemy_id, 'combat_id': 1, 'hp': repr(float(total)), 'block': '0'}])
            action = {'kind': 'end_turn', 'plan': signature, 'member': member, 'turn': 1}
            self._row(history, trace, evidence, action, start)
            remaining = max(0.0, total - outcome['removed'])
            if outcome['turns'] > 1:
                end_state_hp = state.hp if not outcome['won'] else outcome['hp']
                end = state.observation(world, room=room, turn=outcome['turns'],
                                        enemies=[{'id': enemy_id, 'combat_id': 1,
                                                  'hp': repr(float(round(remaining if not outcome['won'] else 1.0, 3))), 'block': '0'}])
                end['hp'] = str(int(max(1, end_state_hp)))
                self._row(history, trace, evidence, {'kind': 'end_turn', 'plan': signature, 'member': member,
                                                     'turn': outcome['turns']}, end)
            if self.log is not None:
                self.log.append((cls, state.act, state.floor, int(state.hp), outcome['won'], round(outcome['removed'] / total, 3),
                                 int(outcome['hp']), outcome['turns'], len(state.deck)))
            if outcome['won']:
                state.hp = max(1, int(outcome['hp']))
                return True, None
            state.hp = 0
            terminal = {'act': state.act, 'floor': state.floor, 'turn': outcome['turns'],
                        'enemies': [{'id': enemy_id, 'combat_id': 1, 'hp': repr(float(round(remaining, 3))),
                                     'max_hp': repr(float(total))}]}
            return False, terminal

        terminal_combat = None
        won = False
        try:
            if probe is not None:
                return self._probe(request, probe)
            for act in range(3):
                state.act = act
                for step in range(STEPS):
                    state.step = step
                    state.floor = floor_of(act, step)
                    if horizon is not None and state.floor > horizon or len(trace) >= limit:
                        status = 'BUDGET'
                        raise _Stop()
                    rooms = world.rooms(act, step, state.path)
                    decide('map', rooms=rooms)
                    room = rooms[trace[-1]['col']]
                    state.floor += 1
                    if room in ('Monster', 'Elite'):
                        tier = min(act, 2)
                        enemy = world.enemies[room][tier]
                        alive, terminal_combat = fight(room, room, enemy, (act, step, room))
                        if not alive:
                            raise _Dead()
                        state.gold += 12 if room == 'Monster' else 30
                        if room == 'Elite':
                            state.relics = state.relics + ['RELIC_%d_%d' % (act, step)]
                        decide('card_reward', offers=world.offers(act, step, room, state.path))
                    elif room == 'RestSite':
                        decide('rest')
                    elif room == 'Shop':
                        decide('shop', offers=world.offers(act, step, room, state.path, count=2))
                    else:
                        decide('event', offers=world.offers(act, step, room, state.path, count=1))
                # Boss of the act (two in the final act).
                state.step = STEPS
                state.floor = 16 * act + 16
                if horizon is not None and state.floor > horizon:
                    status = 'BUDGET'
                    raise _Stop()
                decide('map', rooms=['Boss'])
                if act < 2:
                    alive, terminal_combat = fight('Boss', 'Boss', world.enemies['Boss'][act], (act, 'boss'))
                    if not alive:
                        raise _Dead()
                    state.gold += 75
                    decide('card_reward', offers=world.offers(act, STEPS, 'Boss', state.path))
                    state.hp = min(state.max_hp, state.hp + 0.8 * (state.max_hp - state.hp))
                else:
                    alive, terminal_combat = fight('Boss', 'F1', world.enemies['F1'], (act, 'f1'))
                    if not alive:
                        raise _Dead()
                    state.step = STEPS + 1
                    state.floor = 49
                    decide('map', rooms=['Boss'])
                    alive, terminal_combat = fight('Boss', 'F2', world.enemies['F2'], (act, 'f2'), final_second=True)
                    if not alive:
                        raise _Dead()
                    won = True
        except _Dead:
            status = 'DEAD'
        except _Stop:
            pass
        cost += 0.35 + 0.003 * min(len(history), len(trace))
        result = {'trace': trace, 'decision_evidence': evidence, 'consumed': len(history), 'restored_prefix': 0,
                  'checkpoints': [], 'campaign': {'act_count': 3, 'final_act_boss_count': 2},
                  'reason': None, 'native_terminal_observed': True, 'game_equivalence_verified': False,
                  'performance': {'wall_us': int(cost * 1e6), 'cpu_us': int(cost * 1e6),
                                  'counters': {'prefix_replayed_actions': min(len(history), len(trace)),
                                               'checkpoint_skipped_actions': 0,
                                               'new_actions': max(0, len(trace) - len(history))},
                                  'exclusive_stages': {'beam_search': {'us': sum(s['wall_us'] for s in searches)},
                                                       'prefix_native_execute': {'us': int(3000 * min(len(history), len(trace)))}}},
                  'advisor_metrics': {'searches': searches}}
        if won:
            result.update(status='TERMINAL', value=[1], objective=OBJECTIVE['id'],
                          observation=state.observation(world, room=None))
        elif status == 'DEAD':
            result.update(status='TERMINAL', value=[0], objective=OBJECTIVE['id'], terminal_combat=terminal_combat,
                          observation=dict(evidence[-1]['observation'], hp='0'))
        else:
            result.update(status='BUDGET', value=None, native_terminal_observed=False,
                          observation=state.observation(world, room=None))
        return result, cost

    @staticmethod
    def _row(history, trace, evidence, action, observation):
        index = len(trace)
        if index < len(history) and canonical(history[index]) != canonical(action):
            raise SimInvalid('combat history diverged at %d' % index)
        trace.append(action)
        evidence.append({'phase': 'combat', 'observation': observation, 'available_actions': [action]})

    def _probe(self, request, spec):
        """Synthetic full-HP boss probe from a map boundary (readiness/paired)."""
        history = request['history']
        world = self.world
        observed = spec.get('expected_entry_observation')
        if not isinstance(observed, dict) or not isinstance(observed.get('deck'), list):
            raise SimInvalid('probe without an expected map observation')
        # The boundary state is the recorded map observation (deck, relics,
        # max HP); HP is the probe's full HP. Prefix replay is costed, not rerun.
        state = State()
        state.act, state.floor = 2, 48
        state.deck = [(card['id'], card['upgrade']) for card in observed['deck']]
        for edit in spec.get('edits') or []:
            if edit.get('op') == 'add':
                state.deck = state.deck + [(edit['card'], int(edit.get('upgrade') or 0))]
        state.relics = list(observed.get('relics') or ['BURNING_BLOOD'])
        state.max_hp = int(float(observed.get('max_hp') or 80))
        state.hp = int(spec.get('hp') or state.max_hp)
        state.path = (observed.get('rng') or {}).get('sim_path', 'probe')
        boss = (spec.get('enter') or {}).get('boss')
        cls = 'F2' if boss == 1 else 'F1'
        plan, _ = plan_for(request.get('advisor') or {}, 'Boss', False)
        outcome, spent, members = Fight(world, state, world.enemies[cls], ('probe', cls), cls).solve(
            plan, 'first_win', 'Boss', sample=spec.get('rng') or 0)
        cost = spent + 0.6 + 0.003 * len(history)
        total = world.enemies[cls][0]
        remaining = max(0.0, total - outcome['removed'])
        fight_obs = state.observation(world, room='Boss', turn=1,
                                      enemies=[{'id': cls, 'combat_id': 1, 'hp': repr(float(total)), 'block': '0'}])
        decision = {'status': 'PROBE', 'synthetic': True, 'value': None, 'reason': None,
                    'native_terminal_observed': False, 'consumed': len(history),
                    'trace': deepcopy(history) + [{'kind': 'probe_enter', 'encounter': cls}, {'kind': 'end_turn'}],
                    'decision_evidence': [{'phase': 'replayed', 'observation': {}, 'available_actions': []}
                                          for _ in history] + [
                        {'phase': 'map', 'observation': deepcopy(observed), 'available_actions': []},
                        {'phase': 'combat', 'observation': fight_obs, 'available_actions': [{'kind': 'end_turn'}]}],
                    'probe': {'entered': True, 'fought': True, 'won': bool(outcome['won']), 'entry_index': len(history),
                              'edits': deepcopy(spec.get('edits') or [])},
                    'performance': {'wall_us': int(cost * 1e6), 'cpu_us': int(cost * 1e6), 'counters': {}},
                    'advisor_metrics': {'searches': [{'expanded_nodes': 1000 * members, 'wall_us': int(spent * 1e6)}]}}
        if outcome['won']:
            decision['observation'] = dict(fight_obs, hp=str(int(outcome['hp'])), turn=None, enemies=None)
        else:
            decision['observation'] = dict(fight_obs, hp='0')
            decision['terminal_combat'] = {'act': 2, 'floor': 48, 'turn': outcome['turns'],
                                           'enemies': [{'id': cls, 'combat_id': 1, 'hp': repr(float(round(remaining, 3))),
                                                        'max_hp': repr(float(total))}]}
        progress = request.get('research_progress')
        if progress is not None:
            decision['research_progress'] = {'schema': 'spire-research-progress/v1',
                                             'baseline_sha256': progress['baseline_sha256'], 'checkpoint_restored': False}
        return decision, cost


class SimInvalid(Exception):
    pass


class _Dead(Exception):
    pass


class _Stop(Exception):
    pass


# ---------------------------------------------------------------- clock and pool
class SimClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += max(0.0, float(seconds))


class SimFuture(Future):
    def __init__(self, clock, finish):
        super().__init__()
        self.clock, self.finish, self.cancel_flag = clock, finish, False

    def done(self):
        return self.cancel_flag or self.clock.now >= self.finish

    def result(self, timeout=None):
        if self.cancel_flag and not super().done():
            from spire_exact.planning.pool import WorkerError
            raise WorkerError('SEARCH_CANCELLED')
        return super().result(timeout)


def sim_wait(clock):
    real_wait = concurrent.futures.wait

    def wait(fs, timeout=None, return_when=concurrent.futures.ALL_COMPLETED):
        fs = list(fs)
        sims = [f for f in fs if isinstance(f, SimFuture)]
        if not sims:
            return real_wait(fs, timeout=timeout, return_when=return_when)
        if not any(f.done() for f in sims):
            clock.now = max(clock.now, min(f.finish for f in sims))
        done = {f for f in sims if f.done()}
        return concurrent.futures._base.DoneAndNotDoneFutures(done, set(sims) - done)
    return wait


class SimPool:
    """Fake NativePool: synchronous simulation, simulated worker queue."""

    def __init__(self, world, clock, workers=7):
        self.world, self.engine, self.clock = world, Engine(world), clock
        self.inputs = deepcopy(INPUTS)
        self.stamp = {'host_sha256': SHA}
        self.resources = SimpleNamespace(workers=workers, dop=1, reserve_bytes=0,
                                         as_dict=lambda: {'simulated': True, 'workers': workers})
        self.runtime = {'profile': 'simulated'}
        self.lanes = [0.0] * workers
        self.stats = {'submitted': 0, 'completed': 0, 'failed': 0, 'simulated_worker_seconds': 0.0,
                      'queue_timeouts': 0, 'task_timeouts': 0, 'invalid': 0}
        self.futures = []
        self.work = {}

    def research_progress_baseline(self, ctx, out, timeout):
        raw = json.dumps({'schema_version': 0, 'simulated_initial': self.world.seed})
        return {'schema': 'spire-research-progress/v1', 'game_sha256': SHA, 'native_identity': dict(IDENTITY),
                'context': {key: ctx[key] for key in ('seed', 'character', 'ascension', 'unlocks')} | {
                    'information': 'full', 'objective': 'whole_run_victory/v1'},
                'baseline_snapshot': raw, 'baseline_sha256': hashlib.sha256(raw.encode()).hexdigest()}

    def validate_inputs(self, request):
        return None

    def _schedule(self, compute, timeout, kind):
        from spire_exact.planning.pool import WorkerError
        submitted = self.clock.now
        lane = min(range(len(self.lanes)), key=lambda i: (self.lanes[i], i))
        start = max(submitted, self.lanes[lane])
        self.stats['submitted'] += 1
        if start - submitted >= timeout:
            future = SimFuture(self.clock, submitted + timeout)
            future.set_exception(WorkerError('QUEUE_TIMEOUT'))
            self.stats['queue_timeouts'] += 1
            self.futures.append(future)
            return future
        try:
            value, cost = compute()
        except SimInvalid as error:
            self.stats['invalid'] += 1
            value, cost = None, 0.5
            failure = WorkerError('INVALID_STATE: ' + str(error))
        else:
            failure = None
        allowed = timeout - (start - submitted)
        if failure is None and cost > allowed:
            failure, cost = WorkerError('NATIVE_TASK_TIMEOUT'), allowed
            self.stats['task_timeouts'] += 1
        finish = start + cost
        self.lanes[lane] = finish
        self.stats['simulated_worker_seconds'] += cost
        self.work[kind] = self.work.get(kind, 0.0) + cost
        future = SimFuture(self.clock, finish)
        if failure is None:
            future.set_result(value)
            self.stats['completed'] += 1
        else:
            future.set_exception(failure)
            self.stats['failed'] += 1
        self.futures.append(future)
        return future

    def submit(self, request, out, timeout, *, fresh=False, disposable=False, **_):
        kind = 'consumer' if disposable else 'evaluation'

        def compute():
            result, cost = self.engine.run(deepcopy(request))
            return (result, dict(IDENTITY)), cost + (2.0 + 0.003 * len(request.get('history') or []) if disposable else 0.0)
        return self._schedule(compute, timeout, kind)

    def submit_probes(self, requests, out, timeout):
        def compute():
            rows, total = [], 0.0
            for request in requests:
                try:
                    decision, cost = self.engine.run(deepcopy(request), probe=request.get('probe') or {})
                    rows.append((decision, None))
                    total += cost
                except SimInvalid as error:
                    rows.append((None, 'INVALID_STATE: ' + str(error)))
                    total += 0.5
            return rows, total + 1.0
        return self._schedule(compute, timeout, 'probe')

    def run(self, request, out, timeout=90, *, fresh=False, **_):
        """Synchronous fresh replay (verification); advances the clock."""
        result, cost = self.engine.run(deepcopy(request))
        # Fresh process plus a full native replay of every action, no checkpoint.
        cost += 2.0 + 0.012 * len(request.get('history') or [])
        self.clock.advance(cost)
        self.work['verification'] = self.work.get('verification', 0.0) + cost
        return result, dict(IDENTITY)

    def cancel_pending(self):
        for future in self.futures:
            if not future.done():
                future.cancel_flag = True


@contextmanager
def simulated_time(clock):
    """Patch the planner's clocks and concurrent.futures.wait to the simulated clock."""
    from spire_exact.planning import search
    import time as real_time
    shim = SimpleNamespace(perf_counter=clock, sleep=clock.advance, process_time=real_time.process_time,
                           time=real_time.time)
    with mock.patch.object(search, 'perf_counter', clock), mock.patch.object(search, 'time', shim), \
            mock.patch.object(search, 'runtime_snapshot', lambda: {}), \
            mock.patch.object(concurrent.futures, 'wait', sim_wait(clock)):
        yield


def base_advisor():
    return {'budget_ms': 600000, 'boss_budget_ms': 600000, 'nodes': 60000, 'profile': 'Low', 'dop': 1,
            'binary_identity': {'SimHost.dll': SHA}}


def worlds(count=None, start=1000):
    """The predeclared mix: (seed, class) pairs in a fixed order."""
    rows = []
    for kind, number in MIX:
        for index in range(number):
            rows.append((start + len(rows), kind))
    return rows if count is None else rows[:count]


# ---------------------------------------------------------------- planner runs
ARMS = {
    # holdout-01's frozen-i054a configuration on the current code base
    # (legacy entry plus its explicit flags; no paired tables existed then).
    'i054a': ['--feature-profile', 'legacy', '--scheduler', 'focus', '--prior', '--gate-preset', 'escalate',
              '--repair-mode', 'gate', '--normal-nodes', '10000', '--root-policies', 'pick,elo',
              '--focus-cluster-cap', '2', '--final-gate-plan', 'open', '--no-paired-card-probes'],
    'i082': ['--feature-profile', 'i082'],
    'i085': ['--feature-profile', 'i085'],
    'i085-tail': ['--feature-profile', 'i085', '--tail-mode', 'on'],
    'final01': ['--feature-profile', 'i085-final01'],
    # final01 ablations. final01 itself is the lean profile (i081/i082
    # synthetic F2 machinery and paired tables off); each arm changes one thing.
    'final01-noprior': ['--feature-profile', 'i085-final01', '--strategy-prior', 'off', '--no-clinic-hints'],
    'final01-nohint': ['--feature-profile', 'i085-final01', '--no-clinic-hints'],
    'final01-noclinic': ['--feature-profile', 'i085-final01', '--no-clinic', '--no-clinic-hints', '--strategy-prior', 'off'],
    'final01-readiness': ['--feature-profile', 'i085-final01', '--f2-readiness-probes'],
    'final01-i081': ['--feature-profile', 'i085-final01', '--f2-readiness-probes', '--f2-dead-retry',
                     '--f2-joint-focus', '--f2-joint-model', '--paired-card-probes', '--paired-card-joint'],
    'final01-no-i075': ['--feature-profile', 'i085-final01', '--no-f1-winner-reuse', '--no-prefer-f1-hp',
                        '--no-gold-shop-routes', '--no-low-hp-routes', '--no-lean-third-act', '--no-shop-preparation'],
    # Native-relevant arms the simulator models poorly (Coordinator members are
    # neutral samples here; isolated auxiliary consumers are inert).
    'final01-escalate': ['--feature-profile', 'i085-final01', '--gate-preset', 'escalate'],
    'final01-nogov': ['--feature-profile', 'i085-final01', '--clinic-aux-share', '100', '--clinic-aux-contended', '100'],
    'final01-window28': ['--feature-profile', 'i085-final01', '--dispatch-window', '28'],
}


def planner_argv(seed, out, minutes, workers, solver_seed, arm_args):
    seconds = minutes * 60
    return ['--seed', str(seed), '--out', str(out), '--character', 'IRONCLAD', '--ascension', '10', '--unlocks', 'all',
            '--workers', str(workers), '--dop', '1', '--evaluations', '1000000', '--seconds', str(max(10, seconds - 30)),
            '--task-seconds', str(min(1200, seconds)), '--max-decisions', '12000', '--lookahead-actions', '12000',
            '--lookahead-floors', '99', '--alternatives', '8', '--survivors', '2', '--budget-ms', '600000',
            '--boss-budget-ms', '600000', '--nodes', '60000', '--profile', 'Low', '--dispatch', 'ordered',
            '--dispatch-window', '56', '--solver-seed', str(solver_seed), '--low-io', '--event-driven-settle',
            '--checkpoint-mib', '1024', '--cache-mib', '128', '--archive-entries', '256', *arm_args]


def simulate(world, arm, *, minutes=45, workers=7, solver_seed=271828, out=None, extra=()):
    """Run the real planner loop against one synthetic world. Returns a summary."""
    import tempfile
    from pathlib import Path
    from spire_exact.mode1 import context
    from spire_exact.planning import search
    from spire_exact.planning.__main__ import parse_planner, search_config, configure_advisor
    arm_args = list(ARMS[arm]) + list(extra)
    with tempfile.TemporaryDirectory() as scratch:
        directory = Path(out) if out is not None else Path(scratch) / 'run'
        argv = planner_argv(world.seed, directory, minutes, workers, solver_seed, arm_args)
        _, args, gates, schedule, policies = parse_planner(argv)
        config = search_config(args, gates, schedule, policies)
        advisor = configure_advisor(base_advisor(), args, gates, 1)
        ctx = context(str(world.seed), 'IRONCLAD', 10, 'all')
        clock = SimClock()
        pool = SimPool(world, clock, workers)
        with simulated_time(clock):
            report = search.solve(ctx, directory, pool, config, advisor=advisor)
        return summarize(world, arm, report, pool, clock, minutes)


def summarize(world, arm, report, pool, clock, minutes):
    records = report.get('evaluations') or []
    kinds = {}
    for record in records:
        kinds[record.get('kind')] = kinds.get(record.get('kind'), 0) + 1
    first = {}
    for record in records:
        observation = record.get('observation') or {}
        floor = observation.get('floor')
        if type(floor) is int:
            for name, threshold in (('f1_entry', 48), ('f2_entry', 49)):
                if floor >= threshold and name not in first:
                    first[name] = record.get('completed_wall_seconds')
    verified = report.get('status') == 'VERIFIED_WIN_IN_NATIVE_HOST'
    metrics = report.get('search_metrics') or {}
    clinic = metrics.get('gate_clinic') or {}
    return {'sim_version': SIM_VERSION, 'world': world.seed, 'kind': world.kind, 'arm': arm, 'minutes': minutes, 'verified': verified,
            'verified_seconds': report.get('elapsed_seconds') if verified else None,
            'stop_reason': report.get('stop_reason'), 'evaluations': report.get('evaluations_total'),
            'kinds': kinds, 'first_f1_entry_seconds': first.get('f1_entry'), 'first_f2_entry_seconds': first.get('f2_entry'),
            'simulated_seconds': clock.now, 'worker_seconds_by_kind': {k: round(v, 1) for k, v in pool.work.items()},
            'pool': dict(pool.stats), 'best_observation': {k: (report.get('best_observation') or {}).get(k)
                                                            for k in ('act', 'floor', 'hp')},
            'clinic_verdicts': clinic.get('counts'), 'clinic_modes': clinic.get('focus_modes'),
            'aux_governor': metrics.get('aux_governor'), 'scope': 'synthetic world; not native evidence'}
