"""Search mechanisms.

A mechanism observes absorbed results and queues requests: on the search's
urgent queue (served first) or its failure-repair queue. It never chooses what
runs next (the allocator in core.py does) and never touches the loop. Every
mechanism here is frozen-i054a-shm behaviour, moved out of the former
search.solve() unchanged.

Under the i054a configuration only GateRetry produces evaluations; the others
are kept for the configurations the tests cover.
"""
from __future__ import annotations
from collections import Counter

from ..canonical import canonical, ContractError
from .archive import classify_failure, failure_combat_prefix, combat_loss_progress, utility
from .probes import gate_entries, probe_request, edit_candidates, offered_cards
from .repairs import deep_repair


def near_miss(result, percent):
    """Allocation signal only: was the fatal fight close enough to re-solve?"""
    progress = combat_loss_progress(result)
    if progress.get('revivals_observed', 0) > 0:
        return True
    fraction = progress.get('current_life_hp_removed_fraction')
    return bool(progress.get('available')) and fraction is not None and 100 * fraction >= percent


def prefix_trie(scheduler):
    """The scheduler's exact prefix trie (full action bytes), if it has one."""
    return getattr(getattr(scheduler, 'explorer', scheduler), 'prefixes', None)


class GateRetry:
    """repair_mode 'gate': re-solve a close fatal fight with the next heavier
    advisor plan; a fight that stays fatal moves on to the next level. Best
    trajectory first; a failed retry proves nothing."""
    kinds = ('gate_retry',)

    def __init__(self, search):
        # fight entry (trie node, or canonical prefix) -> plans already queued
        self.levels = {}

    def observe(self, s, spec, candidate, label, classification, family):
        cfg = s.config
        if classification != 'NATIVE_ROUTE_DEATH' or cfg.scheduler == 'dfs':
            return
        probe = failure_combat_prefix(candidate)
        if not probe:
            return
        trie = prefix_trie(s.scheduler)
        key = trie.index(probe['prefix'])[-1] if trie is not None else canonical(probe['prefix'])
        level = self.levels.get(key, 0)
        if level < len(cfg.gate_retry_plans) and near_miss(candidate, cfg.gate_retry_percent):
            self.levels[key] = level + 1
            s.repairs.append({'kind': 'gate_retry', 'category': 'failure', 'priority': utility(candidate), 'family': family,
                'repair': {'source': label, 'floor': probe['floor'], 'room': probe['entry_observation'].get('room'), 'level': level + 1},
                'request': s.ev.request(probe['prefix'], policy=0, horizon=probe['floor'] + cfg.lookahead_floors,
                    actions=min(cfg.max_decisions, len(probe['prefix']) + cfg.lookahead_actions),
                    advisor_patch=cfg.gate_retry_plans[level])})


class CombatProbe:
    """repair_mode fifo / deep_boss / deep_final_boss: re-solve each fatal fight
    once with three times the advisor budget (deep boss repairs also get the
    boss repair budget), and continue a probe that is still alive."""
    kinds = ('combat_probe', 'probe_followup')

    def __init__(self, search):
        self.seen = set()

    def observe(self, s, spec, candidate, label, classification, family):
        cfg = s.config
        if classification == 'NATIVE_ROUTE_DEATH' and cfg.scheduler != 'dfs':
            probe = failure_combat_prefix(candidate)
            if probe and (key := canonical(probe['prefix'])) not in self.seen:
                self.seen.add(key)
                deep = deep_repair(cfg.repair_mode, probe['entry_observation'])
                s.repairs.append({'kind': 'combat_probe', 'category': 'failure', 'family': family,
                    'repair': {'source': label, 'floor': probe['floor'], 'room': probe['entry_observation'].get('room'), 'deep': deep},
                    'request': s.ev.request(probe['prefix'], policy=0, horizon=probe['floor'],
                        actions=min(cfg.max_decisions, len(probe['prefix']) + cfg.lookahead_actions), advisor_scale=3,
                        boss_budget_ms=cfg.boss_repair_ms if deep else None)})
        if spec['kind'] == 'combat_probe' and classification in ('SEARCH_BUDGET', 'DECISION_BOUNDARY'):
            prefix = candidate.get('trace', [])
            key = canonical(prefix)
            if prefix and len(prefix) < cfg.max_decisions and key not in s.seen_rollouts:
                s.seen_rollouts.add(key)
                s.urgent.append({'kind': 'probe_followup', 'category': 'preparation', 'family': family, 'request': s.ev.request(prefix)})


class Deepen:
    """After each collect, continue the best macro results that stopped at a
    budget or decision boundary (none do under i054a's 99-floor lookahead)."""
    kinds = ('deepen',)

    def __init__(self, search):
        pass

    def after_collect(self, s, trials):
        cfg = s.config
        viable = [t for t in trials if t[0]['kind'].startswith('macro_') and
                  classify_failure(t[1]) in ('SEARCH_BUDGET', 'DECISION_BOUNDARY')]
        for spec, candidate, label in sorted(viable, key=lambda x: utility(x[1]), reverse=True)[:cfg.survivors]:
            prefix = candidate.get('trace', [])
            key = canonical(prefix)
            if prefix and len(prefix) < cfg.max_decisions and key not in s.seen_rollouts:
                s.seen_rollouts.add(key)
                s.urgent.append({'kind': 'deepen', 'category': 'exploration', 'family': s.families.get(label), 'request': s.ev.request(prefix)})


class GateProbes:
    """Synthetic gate probes (probes.py): when a boss has enough distinct real
    entries, one table of one-card edits is probed from the lost entry that
    came closest. Outcomes feed the gate model only: never a trajectory,
    checkpoint, cached result, bound or proof."""
    kinds = ('gate_probe',)

    def __init__(self, search):
        self.trie = prefix_trie(search.scheduler)
        if self.trie is None:
            raise ContractError('gate probes need a scheduler with the exact prefix trie')
        # per boss the lost real entry that came closest (the base of the next
        # table), tables already scheduled, cards this seed offered
        self.bases = {}
        self.tables = Counter()
        self.offered = Counter()

    def absorb_batch(self, s, spec, candidate):
        s.models.add_probes(spec['gate'], spec['table'], list(zip(spec['labels'], candidate['outcomes'])))

    def observe(self, s, spec, candidate, label, classification, family):
        if not candidate.get('trace'):
            return
        cfg = s.config
        searched = len(spec['request']['history'])
        self.offered.update(offered_cards(candidate, since=searched))
        for entry in gate_entries(candidate):
            # Only fights this evaluation searched itself, and only lost ones:
            # a table asks what would have helped the entry that came closest.
            if entry['index'] < searched or entry['lost'] is None:
                continue
            known = self.bases.get(entry['gate'])
            if known is None or entry['lost'][0] > known['removed']:
                self.bases[entry['gate']] = {'removed': entry['lost'][0], 'node': self.trie.index(candidate['trace'][:entry['enter']])[-1],
                    'enter': candidate['trace'][entry['enter']], 'entry': entry['entry'], 'label': label}
        for gate_id, gate in sorted(s.models.gates.items()):
            done = self.tables[gate_id]
            if done < len(cfg.gate_probe_schedule) and len(gate.entries) >= cfg.gate_probe_schedule[done] and gate_id in self.bases:
                self.tables[gate_id] += 1
                s.urgent.extend(self.specs(s, gate_id, done))

    def specs(self, s, gate_id, table):
        """One table as evaluation-sized batches: the unedited base first, then every one-card edit."""
        cfg = s.config
        base = self.bases[gate_id]
        trace = self.trie.restore(base['node']) + [base['enter']]
        entry = {'enter': len(trace) - 1}
        template = s.ev.request([])
        candidates = [(None, [])] + edit_candidates(base['entry'], self.offered, cfg.gate_probe_cards)
        specs = []
        for start in range(0, len(candidates), cfg.gate_probe_chunk):
            chunk = candidates[start:start + cfg.gate_probe_chunk]
            specs.append({'kind': 'gate_probe', 'category': 'probe', 'gate': gate_id, 'table': table, 'base': base['label'],
                'labels': [name for name, _ in chunk],
                'requests': [probe_request(template, trace, entry, edits, advisor_patch=cfg.gate_probe_plan) for _, edits in chunk],
                'request': {'history': trace[:-1], 'generate_candidate': False}})
        return specs

    def metrics(self, s):
        batches = [r for r in s.ev.records if r.get('kind') == 'gate_probe']
        return {'batches': len(batches), 'probes': sum(r.get('probes', 0) for r in batches), 'usable': sum(r.get('usable_probes', 0) for r in batches),
            'tables_scheduled': {str(list(k)): v for k, v in sorted(self.tables.items())}, 'search_seconds': sum(r.get('probe_search_seconds', 0) for r in batches),
            'scope': 'synthetic; allocation only, never a trajectory, checkpoint, cached result, bound or proof'}
