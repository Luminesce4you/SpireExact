"""Synthetic gate probes: one-card deck edits tried against a real gate fight.

The hand-written rollout policy scores a card from the numbers on its face, and
the per-seed gate model only learns from decks the search already built. A
probe asks the native engine instead: replay a trajectory to the map decision
in front of a boss, add / remove / upgrade one card with the game's own
commands, enter the fight and let the combat solver play it.

A probe state never existed on a real route. Its result is an allocation
signal only: it must run in a worker that is discarded afterwards and it never
enters a trajectory, the checkpoint archive, the result cache, the frontier or
a scheduler. Losing or winning a probe proves nothing about any real state.
"""
from __future__ import annotations
from collections import Counter
from copy import deepcopy
from .archive import combat_loss_progress
from .gatemodel import boss_fight_rows

# Actions a probe may add after the replayed prefix: the entering move and one fight.
PROBE_DECISIONS = 4000


def gate_entries(result: dict) -> list:
    """Boss fights of one trajectory that a probe can re-enter: `boss_fight_rows`
    plus `enter`, the index of the map action that walked into the fight. A
    fight is left out when anything was decided between that move and the
    first combat decision."""
    trace = result.get('trace', [])
    rows = []
    for row in boss_fight_rows(result):
        index = row['index']
        if index < 1 or trace[index - 1].get('kind') != 'map':
            continue
        rows.append({**row, 'enter': index - 1})
    return rows


def probe_request(request: dict, trace: list, entry: dict, edits=(), *, hp: int | None = None, rng: int | None = None,
                  advisor_patch: dict | None = None) -> dict:
    """Native request of one probe. `request` is any generated request of the
    same run (context, advisor configuration, settle mode); `entry` comes from
    `gate_entries` of the trajectory `trace` belongs to."""
    probe = {key: request[key] for key in ('seed', 'character', 'ascension', 'unlocks')}
    history = deepcopy(trace[:entry['enter']])
    # Same policy seed as the run's rollouts: a fight decision the advisor leaves to the
    # fallback scorer is then made exactly as in a real evaluation.
    probe.update(history=history, generate_candidate=True, policy_seed=int(request.get('policy_seed') or 0),
                 max_decisions=len(history) + PROBE_DECISIONS, capture_checkpoints=False, low_io=bool(request.get('low_io')))
    if request.get('event_driven_settle'):
        probe['event_driven_settle'] = True
    if request.get('advisor'):
        probe['advisor'] = deepcopy(request['advisor'])
        if advisor_patch:
            probe['advisor'].update(deepcopy(advisor_patch))
    spec = {'edits': [dict(edit) for edit in edits], 'enter': deepcopy(trace[entry['enter']])}
    if hp is not None:
        spec['hp'] = int(hp)
    if rng is not None:
        spec['rng'] = int(rng)
    probe['probe'] = spec
    return probe


def probe_outcome(result: dict) -> dict | None:
    """What a completed probe measured, or None when the probe did not finish
    a fight. `outcome` is 1 + HP fraction kept after a survived fight; a lost
    fight reports `lost` = (enemy HP removed, enemy HP of every life observed)
    and leaves the common scale to the gate (see GateModels)."""
    if result.get('status') != 'PROBE' or result.get('synthetic') is not True:
        return None
    info = result.get('probe') or {}
    if not info.get('fought') or info.get('won') is None:
        return None
    observation = result.get('observation') or {}
    searches = (result.get('advisor_metrics') or {}).get('searches') or []
    row = {'won': bool(info['won']), 'edits': info.get('edits') or [],
           'nodes': sum(int(s.get('expanded_nodes') or 0) for s in searches),
           'search_seconds': sum(int(s.get('wall_us') or 0) for s in searches) / 1e6}
    if info['won']:
        # Same normalisation as boss_fight_rows: HP kept over the maximum HP at the fight entry
        # (a fight can raise the maximum).
        start = int(info.get('entry_index') or 0) + 1
        entry = next((row.get('observation') for row in (result.get('decision_evidence') or [])[start:]
                      if (row.get('observation') or {}).get('turn') is not None), observation)
        max_hp = max(1.0, float(entry.get('max_hp') or 1))
        hp = max(0.0, float(observation.get('hp') or 0))
        row.update(hp=hp, max_hp=max_hp, outcome=1.0 + hp / max_hp, lost=None)
    else:
        progress = combat_loss_progress(result)
        available = bool(progress.get('available'))
        row.update(hp=0.0, outcome=None, turns=progress.get('turns_observed'), revivals=progress.get('revivals_observed'),
                   lost=(float(progress.get('observed_hp_removed') or 0.0), float(progress.get('observed_life_hp') or 0.0)) if available else (0.0, 0.0))
    return row


def scaled(outcome: dict, life_total: float) -> float:
    """Probe outcome on the gate's common scale (see GateModels._Gate.total)."""
    if outcome['won']:
        return outcome['outcome']
    return min(1.0, outcome['lost'][0] / life_total) if life_total > 0 else 0.0


def edit_candidates(entry: dict, offered: Counter, limit: int = 40) -> list:
    """[(label, edits)] for one entry state: add one copy of the cards this seed
    has offered most often, remove one copy of every card in the deck, upgrade
    one not yet upgraded copy of every card in the deck. Labels are the host's
    decision labels, so a probe value can be read next to the gate model's."""
    deck = entry.get('deck') or []
    rows = []
    for card, _ in sorted(offered.items(), key=lambda item: (-item[1], item[0]))[:limit]:
        rows.append(('card:' + card, [{'op': 'add', 'card': card, 'upgrade': 0}]))
    lowest = {}
    for card in deck:
        level = int(card.get('upgrade') or 0)
        lowest[card['id']] = min(level, lowest.get(card['id'], level))
    for card in sorted(lowest):
        rows.append(('remove:' + card, [{'op': 'remove', 'card': card, 'upgrade': lowest[card]}]))
    for card in sorted(lowest):
        if lowest[card] == 0:
            rows.append(('upgrade:' + card, [{'op': 'upgrade', 'card': card, 'upgrade': 0}]))
    return rows


def offered_cards(result: dict, until: int | None = None, since: int = 0) -> Counter:
    """How often each card was offered (card rewards and shop cards) in the
    labelled decisions of a trajectory, from action index `since` up to
    `until`. `since` = length of the replayed prefix counts every decision of a
    search once, however many rollouts share it."""
    counts = Counter()
    evidence = result.get('decision_evidence', [])
    for row in evidence[since:len(result.get('trace', [])) if until is None else until]:
        for labels in row.get('option_labels') or []:
            for label in labels:
                if label.startswith('card:'):
                    counts[label[5:]] += 1
    return counts
