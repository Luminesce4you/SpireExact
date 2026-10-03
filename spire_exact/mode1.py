"""Witness-first mode1: maximize whole-run victory in {0, 1}.

Candidates may be heuristic and incomplete. Only a fresh, complete successful
replay closes the Boolean bound. Defeats, timeouts and missing transitions never
establish UNSAT. The offline native host's proof scope is kept explicit.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Callable
import heapq
from .canonical import ContractError, canonical, clone, digest, loads, read_json, write_json
from .native import export_native, game_data, input_fingerprint
from .upstream import ROOT, file_hash, verify_upstream

OBJECTIVE = {'id': 'whole_run_victory/v1', 'information': 'full', 'minimum': 0, 'maximum': 1,
             'secondary_objective': None}


@dataclass(frozen=True)
class CandidateBudget:
    attempts: int = 16
    decisions: int = 3000
    seconds: int = 120

    def __post_init__(self):
        for n in (self.attempts, self.decisions, self.seconds):
            if type(n) is not int or n < 1:
                raise ContractError('mode1 budgets must be positive integers')


def context(seed: str, character: str, ascension: int, unlocks: str):
    if not isinstance(seed, str) or not seed.strip():
        raise ContractError('seed must be a nonempty string')
    if type(ascension) is not int or ascension < 0:
        raise ContractError('ascension must be a nonnegative integer')
    if unlocks not in ('all', 'none'):
        raise ContractError('explicit supported unlock context required')
    return {'seed': seed, 'character': character.upper(), 'ascension': ascension,
            'unlocks': unlocks, 'game_mode': 'Standard', 'acts': 'native-default', 'gameplay_mods': [],
            'objective': clone(OBJECTIVE)}


def is_winning_candidate(result: dict) -> bool:
    return (result.get('status') == 'TERMINAL' and result.get('native_terminal_observed') is True
            and result.get('objective') == OBJECTIVE['id'] and result.get('reason') is None
            and type(result.get('value')) is list and len(result['value']) > 0
            and type(result['value'][0]) is int and result['value'][0] == 1)


def check_winning_replay(candidate: dict, replay: dict, candidate_identity: dict, replay_identity: dict) -> dict:
    """Check a *freshly executed* replay, never treat a candidate JSON as a proof."""
    if not is_winning_candidate(candidate) or not is_winning_candidate(replay):
        raise ContractError('both executions must reach an observed native whole-run victory')
    if canonical(candidate_identity) != canonical(replay_identity):
        raise ContractError('backend/version/initial-context identity changed')
    if replay_identity.get('context', {}).get('objective') != OBJECTIVE:
        raise ContractError('proof identity must bind the fixed mode1 objective')
    trace = candidate.get('trace')
    if type(trace) is not list or any(type(a) is not dict for a in trace):
        raise ContractError('candidate must contain a complete action transcript')
    if replay.get('consumed') != len(trace) or canonical(replay.get('trace')) != canonical(trace):
        raise ContractError('replay did not consume exactly the complete candidate transcript')
    for execution in (candidate, replay):
        if type(execution.get('decision_evidence')) is not list or len(execution['decision_evidence']) != len(trace):
            raise ContractError('missing complete decision-by-decision replay evidence')
    if canonical(candidate.get('decision_evidence')) != canonical(replay.get('decision_evidence')):
        raise ContractError('visible decision trajectory diverged during replay')
    if canonical(candidate.get('observation')) != canonical(replay.get('observation')):
        raise ContractError('terminal observation changed during replay')
    return {'schema': 'spire-mode1-certificate/v1', 'objective': clone(OBJECTIVE),
            'identity': clone(replay_identity), 'trace_sha256': digest(trace),
            'trace_length': len(trace), 'lower_bound': 1, 'upper_bound': 1,
            'upper_bound_reason': 'Boolean objective domain: no legal result exceeds 1',
            'proof_scope': 'installed game commands in offline TestMode',
            'optimal_in_backend': True, 'game_equivalence_verified': False,
            'proves_no_better_boolean_value': True, 'proves_highest_score': False,
            'proves_unsatisfiable_seeds': False}


def room_summaries(ctx: dict, result: dict) -> list[dict]:
    """R- contains executed prefixes; R+ stays universal without exclusion proof.

    Observations are diagnostics only. Context plus full action prefix is the
    replayable state reference, not a projected HP/deck equality key.
    """
    trace, evidence = result.get('trace', []), result.get('decision_evidence', [])
    summaries = []
    start = 0
    for i in range(1, min(len(trace), len(evidence))):
        before, after = evidence[start].get('observation') or {}, evidence[i].get('observation') or {}
        if (before.get('act'), before.get('floor')) == (after.get('act'), after.get('floor')):
            continue
        summaries.append({'schema': 'spire-room-summary/v1',
                          'entry': {'context': ctx, 'history': trace[:start]},
                          'verified_transitions': [],
                          'candidate_transitions': [{'history_suffix': trace[start:i], 'exit_observation': after}],
                          'possible_results': {'kind': 'unconstrained', 'victory_upper_bound': 1},
                          'exclusion_certificates': [], 'query_status': 'UNKNOWN'})
        start = i
    return summaries


class NativeCampaignBackend:
    def __init__(self, ctx: dict, directory: Path, game_dir: Path | None, advisor: str = 'none'):
        self.ctx = ctx
        self.directory = directory
        self.data = game_data(game_dir)
        self.inputs = input_fingerprint(self.data)
        self.advisor = None
        if advisor == 'combatsolver':
            upstream = ROOT / 'vendor/CombatSolver'
            provenance = verify_upstream(upstream, allow_dirty=True)
            solver = upstream / '.godot/mono/temp/bin/Release/CombatSolver.dll'
            harness = upstream / 'tools/OfflineSearchHarness/bin/Release/net9.0/OfflineSearchHarness.dll'
            import json
            version = json.loads((self.data.parent / 'release_info.json').read_text(encoding='utf-8'))['version'].removeprefix('v')
            workshop = self.data.parents[2] / 'workshop/content/2868840/3747602295'
            dirs = [workshop / 'compat' / version, workshop / 'shared']
            for file in (solver, harness, dirs[0] / 'STS2-RitsuLib.dll'):
                if not file.is_file():
                    raise ContractError('optional CombatSolver advisor missing: ' + str(file))
            self.advisor = {'solver': str(solver), 'harness': str(harness),
                            'dependency_dirs': [str(p) for p in dirs], 'budget_ms': 200}
            from .planning.identity import advisor_identity
            self.advisor['binary_identity'] = advisor_identity(self.advisor)
            write_json(directory / 'advisor-provenance.json', {'upstream': provenance,
                'solver_sha256': file_hash(solver), 'harness_sha256': file_hash(harness),
                'use': 'candidate proposals only; never an upper bound or infeasibility proof'})
        elif advisor != 'none':
            raise ContractError('unknown candidate advisor')

    def execute(self, label: str, *, policy: int = 0, decisions: int = 3000,
                history: list | None = None, prefix: list | None = None, timeout_seconds: int = 90) -> tuple[dict, dict]:
        if input_fingerprint(self.data) != self.inputs:
            raise ContractError('native inputs changed during mode1 run')
        out = self.directory / label
        params = {k: self.ctx[k] for k in ('seed', 'character', 'ascension', 'unlocks')}
        if history is None and self.advisor is not None:
            params['advisor'] = self.advisor
        try:
            export_native('replay', out, self.data, history=history if history is not None else (prefix or []), generate_candidate=history is None,
                          max_decisions=decisions, policy_seed=policy, timeout_seconds=timeout_seconds, **params)
        except ContractError as error:
            # A native crash is UNKNOWN, but its pre-action journal can propose an
            # alternative. These records never enter the verified-witness set.
            if history is not None or not (out / 'data/decision-evidence.jsonl').is_file():
                raise
            evidence = [loads(line) for line in (out / 'data/decision-evidence.jsonl').read_text(encoding='utf-8').splitlines()]
            trace = [loads(line) for line in (out / 'data/trace.jsonl').read_text(encoding='utf-8').splitlines()]
            size = min(len(trace), len(evidence))
            result = {'status': 'UNSUPPORTED', 'native_terminal_observed': False, 'value': None,
                      'objective': OBJECTIVE['id'], 'reason': str(error), 'trace': trace[:size],
                      'decision_evidence': evidence[:size], 'observation': evidence[size-1]['observation'] if size else None,
                      'journal_only': True, 'game_equivalence_verified': False}
            write_json(out / 'recovered-candidate.json', result)
            identity = {'context': clone(self.ctx), 'native': read_json(out / 'data/identity.json')}
            return result, identity
        result = read_json(out / 'data/decision.json')
        identity = {'context': clone(self.ctx), 'native': read_json(out / 'data/identity.json')}
        return result, identity


def solve_mode1(ctx: dict, output: Path, budget: CandidateBudget = CandidateBudget(),
                game_dir: Path | None = None, backend=None, progress: Callable[[dict], None] | None = None,
                advisor: str = 'none', initial_prefix: list | None = None) -> dict:
    if ctx.get('objective') != OBJECTIVE:
        raise ContractError('mode1 fixes a Boolean victory objective; scores cannot replace it')
    if output.exists() and any(output.iterdir()):
        raise ContractError('mode1 output directory must be empty')
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / 'context.json', ctx)
    backend = backend or NativeCampaignBackend(ctx, output, game_dir, advisor)
    started = perf_counter()
    proposals = []
    proposal_keys = set()
    serial = 0
    deferred_floors = set()
    if initial_prefix:
        proposals.append((0, serial, clone(initial_prefix)))
        proposal_keys.add(canonical(initial_prefix))
    result = {'schema': 'spire-mode1-result/v1', 'context': clone(ctx), 'status': 'UNKNOWN',
              'lower_bound': 0, 'lower_bound_source': 'Boolean domain minimum, not a winning witness',
              'upper_bound': 1, 'optimality_gap': 1, 'best_verified_value': None,
              'optimal_in_backend': False, 'game_equivalence_verified': False,
              'proof_scope': 'installed game commands in offline TestMode',
              'objective': clone(OBJECTIVE), 'attempts': [], 'unresolved_regions':
              [{'kind': 'all_unexcluded_runs', 'upper_bound': 1, 'reason': 'no verified winning witness yet'}],
              'pruned_regions': [], 'stop_reason': None}
    for policy in range(budget.attempts):
        remaining = int(budget.seconds - (perf_counter() - started))
        if remaining <= 0:
            result['stop_reason'] = 'time_budget'
            break
        record = {'policy': policy, 'label': f'candidate-{policy:03d}'}
        try:
            prefix = heapq.heappop(proposals)[2] if proposals else []
            record['prefix_length'] = len(prefix)
            candidate, identity = backend.execute(record['label'], policy=policy, decisions=budget.decisions, prefix=prefix,
                                                  timeout_seconds=min(90, remaining))
            record.update(status=candidate.get('status'), reason=candidate.get('reason'),
                          decisions=len(candidate.get('trace', [])), observation=candidate.get('observation'))
            write_json(output / f'rooms-{policy:03d}.json', room_summaries(ctx, candidate))
            if candidate.get('status') == 'UNSUPPORTED':
                failed_floor = (candidate.get('observation') or {}).get('floor')
                if failed_floor is not None:
                    deferred_floors.add(failed_floor)
                    # Runtime coverage gap: spend candidate budget on earlier choices.
                    # This is not a semantic no-good and never changes the bound.
                    proposals = [p for p in proposals if -p[0] not in deferred_floors]
                    heapq.heapify(proposals)
                    result['deferred_candidate_floors'] = sorted(deferred_floors)
            # Revisit valuable *decisions*, not all reachable states or all battle exits.
            # Evicting a proposal is heuristic scheduling, never an exclusion proof.
            trace = candidate.get('trace', [])
            for i, evidence in enumerate(candidate.get('decision_evidence', [])):
                if i >= len(trace) or evidence.get('phase') not in ('event', 'map', 'rest', 'card_reward', 'shop'):
                    continue
                for action in evidence.get('available_actions', []):
                    if canonical(action) == canonical(trace[i]):
                        continue
                    proposal = trace[:i] + [action]
                    key = canonical(proposal)
                    if key in proposal_keys:
                        continue
                    proposal_keys.add(key)
                    serial += 1
                    floor = (evidence.get('observation') or {}).get('floor', 0)
                    if floor in deferred_floors:
                        continue
                    heapq.heappush(proposals, (-floor, serial, proposal))
            if len(proposals) > 64:
                proposals = heapq.nsmallest(64, proposals)
                heapq.heapify(proposals)
            result['queued_proposals'] = len(proposals)
            if is_winning_candidate(candidate):
                remaining = int(budget.seconds - (perf_counter() - started))
                if remaining <= 0:
                    record['verification'] = 'deferred_time_budget'
                else:
                    # New native process, heuristic selection disabled, all choices replayed.
                    replay, replay_identity = backend.execute(f'verify-{policy:03d}',
                        history=candidate['trace'], decisions=budget.decisions, timeout_seconds=min(90, remaining))
                    certificate = check_winning_replay(candidate, replay, identity, replay_identity)
                    write_json(output / 'certificate.json', certificate)
                    write_json(output / 'winning-route.json', {'context': ctx, 'identity': identity,
                        'trace': candidate['trace'], 'candidate': record['label'], 'replay': f'verify-{policy:03d}'})
                    result.update(status='VERIFIED_WIN_IN_NATIVE_HOST', lower_bound=1,
                                  lower_bound_source='independently re-executed complete winning transcript',
                                  best_verified_value=1, optimality_gap=0, optimal_in_backend=True,
                                  unresolved_regions=[], stop_reason='boolean_upper_bound_reached')
                    record['verification'] = 'passed'
        except (ContractError, OSError) as error:
            record.update(status='UNKNOWN', reason=str(error))
        result['attempts'].append(record)
        write_json(output / 'result.json', result)
        if progress:
            progress(record)
        if result['optimal_in_backend']:
            break
    if result['stop_reason'] is None:
        result['stop_reason'] = 'candidate_budget'
    write_json(output / 'result.json', result)
    write_json(output / 'pending-proposals.json', {'context': ctx, 'proof_status': 'unverified_proposals',
               'proposals': [p[2] for p in sorted(proposals)]})
    return result


def verify_mode1(route_file: Path, output: Path, game_dir: Path | None = None) -> dict:
    route = read_json(route_file)
    if route['context'].get('objective') != OBJECTIVE:
        raise ContractError('route objective is not mode1')
    if output.exists() and any(output.iterdir()):
        raise ContractError('verification output must be empty')
    backend = NativeCampaignBackend(route['context'], output, game_dir)
    replay, identity = backend.execute('replay', history=route['trace'])
    if canonical(identity) != canonical(route['identity']):
        raise ContractError('route identity differs from current game/host/context')
    if not is_winning_candidate(replay) or replay.get('consumed') != len(route['trace']):
        raise ContractError('route did not replay to native whole-run victory')
    if canonical(replay['trace']) != canonical(route['trace']):
        raise ContractError('action transcript changed')
    certificate = check_winning_replay(replay, replay, identity, identity)
    write_json(output / 'certificate.json', certificate)
    return certificate
