"""Schedule already computed F1 winners after a real F2 route death.

This expands candidate routes. It neither enumerates combat exits nor excludes
any unresolved run. A forecast is an unverified plan; only native execution and
the existing fresh whole-run replay can establish a winning route.
"""
from copy import deepcopy
from collections import Counter
import re
from ..canonical import canonical, ContractError


class F1WinnerReuse:
    def __init__(self, enabled=False, *, prefer_hp=False):
        self.enabled = bool(enabled)
        self.prefer_hp = bool(prefer_hp)
        self.seen = set()
        self.observed_f2_deaths = 0
        self.proposals = 0
        self.duplicates = 0
        self.invalid_artifacts = 0
        self.artifact_rejections = Counter()
        self.capture_labels = set()
        self.captured_winner_members = 0
        self.selected_winner_members = 0
        self.selected_plans_marked = 0
        self.captured_f1_roots = 0
        self.execution_labels = set()
        self.consumer_statuses = Counter()
        self.consumer_reasons = Counter()
        self.guard_mismatch_reasons = Counter()
        self.consumers_requested = 0
        self.guard_attempts = 0
        self.entry_guards_passed = 0
        self.loaded_routes = 0
        self.native_f1_wins = 0
        self.native_f2_entries = 0
        self.native_win_candidates = 0
        self.unknown_consumers = 0
        self.f1_search_audits_known = 0
        self.f1_search_audits_unknown = 0
        self.f1_member_search_records = 0

    @staticmethod
    def _context(request):
        return {key: request[key] for key in ('seed', 'character', 'ascension', 'unlocks')} | {
            'information': 'full', 'objective': 'whole_run_victory/v1'}

    @staticmethod
    def _rejection_reason(artifact, result, template, metadata, *, allow_selected=False):
        if not isinstance(artifact, dict) or artifact.get('schema') != 'spire-f1-winner/v1':
            return 'artifact_schema'
        forecast = artifact.get('forecast')
        if (type(artifact.get('selected')) is not bool or artifact['selected'] and not allow_selected
                or not isinstance(forecast, dict) or forecast.get('won') is not True):
            return 'not_an_unselected_predicted_winner'
        entry = artifact.get('entry_history')
        actions = artifact.get('route_actions')
        continuations = artifact.get('continuations')
        if not isinstance(entry, list) or not entry or not isinstance(actions, list) or not actions:
            return 'missing_complete_route_or_prefix'
        if not isinstance(continuations, list) or not isinstance(artifact.get('root_state_text'), str):
            return 'missing_continuation_or_state_text'
        if not artifact['root_state_text'] or not isinstance(artifact.get('entry_observation'), dict):
            return 'missing_root_observation'
        if not isinstance(artifact.get('native_identity'), dict) or not artifact['native_identity']:
            return 'missing_native_identity'
        if not isinstance(artifact.get('native_progress'), dict):
            return 'missing_progress_guard'
        if canonical(artifact['native_identity']) != canonical(metadata.get('native_identity')):
            return 'native_identity'
        if artifact.get('encounter') not in (metadata.get('native_boss_wins') or []):
            return 'source_f1_win_not_observed'
        if type(artifact.get('member_index')) is not int or type(artifact.get('entry_turn')) is not int:
            return 'invalid_member_or_turn'
        if canonical(artifact['context']) != canonical(F1WinnerReuse._context(template)):
            return 'context'
        expected = (template.get('advisor') or {}).get('binary_identity')
        if not expected or canonical(artifact.get('advisor_binary_identity')) != canonical(expected):
            return 'advisor_binary_identity'
        if canonical(result.get('trace', [])[:len(entry)]) != canonical(entry):
            return 'prefix'
        return None

    @staticmethod
    def _validate(artifact, result, template, metadata):
        try:
            return F1WinnerReuse._rejection_reason(artifact, result, template, metadata) is None
        except (ContractError, KeyError, TypeError, ValueError):
            return False

    def _capture_diagnostics(self, result, label):
        if label in self.capture_labels:
            return
        self.capture_labels.add(label)
        meta = result.get('f1_winner_reuse') or {}
        deployment = (meta.get('deployment') or {}) if isinstance(meta, dict) else {}
        roots = deployment.get('captured_f1_root_count') if isinstance(deployment, dict) else None
        if type(roots) is int and roots >= 0:
            self.captured_f1_roots += roots
        for artifact in result.get('f1_winner_candidates') or []:
            forecast = artifact.get('forecast') if isinstance(artifact, dict) else None
            if not isinstance(forecast, dict) or forecast.get('won') is not True:
                continue
            self.captured_winner_members += 1
            self.selected_winner_members += int(artifact.get('selected') is True)

    def _reject(self, reason):
        self.invalid_artifacts += 1
        self.artifact_rejections[reason] += 1

    @staticmethod
    def _plan_bytes(artifact):
        return canonical({field: artifact[field] for field in (
            'entry_history', 'context', 'native_identity', 'advisor_binary_identity',
            'entry_observation', 'native_progress', 'root_state_text', 'route_actions', 'continuations')})

    @staticmethod
    def _predicted_hp(artifact):
        # Only the native explicit integer projection is a sorting signal.
        # Missing/invalid values stay eligible at the end; never infer HP by
        # parsing the audit JSON or use this projection as a state identity.
        forecast = artifact.get('forecast') if isinstance(artifact, dict) else None
        hp = forecast.get('hp') if isinstance(forecast, dict) else None
        return hp if type(hp) is int and hp > 0 else None

    def _ordering_key(self, pair):
        source_order, artifact = pair
        member = artifact.get('member_index') if isinstance(artifact, dict) else None
        member = member if type(member) is int else 2**31
        if not self.prefer_hp:
            return member, source_order
        hp = self._predicted_hp(artifact)
        return hp is None, -hp if hp is not None else 0, member, source_order

    def observe(self, result, label, template_request, *, source_request=None):
        if not self.enabled or not template_request.get('advisor') or result.get('synthetic'):
            return []
        self._capture_diagnostics(result, label)
        source_request = source_request if source_request is not None else template_request
        # The fixed root binds the run and binaries. Policy and tactical plans
        # come from the actual source request, not a newly generated root policy.
        try:
            if canonical(self._context(source_request)) != canonical(self._context(template_request)):
                self._reject('source_context'); return []
            if canonical((source_request.get('advisor') or {}).get('binary_identity')) != canonical(template_request['advisor'].get('binary_identity')):
                self._reject('source_advisor_binary_identity'); return []
            if canonical(source_request.get('research_progress')) != canonical(template_request.get('research_progress')):
                self._reject('source_progress_contract'); return []
        except (ContractError, KeyError, TypeError, ValueError):
            self._reject('source_request_contract'); return []
        value = result.get('value')
        metadata = result.get('f1_winner_reuse') or {}
        if not isinstance(metadata, dict):
            self._reject('metadata_contract')
            return []
        if (result.get('status') != 'TERMINAL' or result.get('reason') is not None
                or not isinstance(value, list) or not value or type(value[0]) is not int or value[0] != 0
                or not metadata.get('final_second_encounter')
                or metadata.get('terminal_encounter') != metadata['final_second_encounter']):
            return []
        self.observed_f2_deaths += 1
        rows = result.get('f1_winner_candidates') or []
        if not isinstance(rows, list):
            self._reject('candidate_list_contract')
            return []
        ordered = sorted(enumerate(rows), key=self._ordering_key)
        # Mark the source's selected plan first, independent of member ordering.
        # An unselected member with exactly the same full plan is not a different
        # F1 proposal. This is attempted-plan scheduling, not infeasibility proof.
        for _, artifact in ordered:
            if not isinstance(artifact, dict) or artifact.get('selected') is not True:
                continue
            try:
                if self._rejection_reason(artifact, result, template_request, metadata, allow_selected=True) is None:
                    key = self._plan_bytes(artifact)
                    self.selected_plans_marked += int(key not in self.seen)
                    self.seen.add(key)
            except (ContractError, KeyError, TypeError, ValueError):
                self._reject('selected_artifact_contract')
        specs = []
        for _, artifact in ordered:
            if isinstance(artifact, dict) and artifact.get('selected') is True:
                continue
            try:
                if (reason := self._rejection_reason(artifact, result, template_request, metadata)) is not None:
                    self._reject(reason)
                    continue
                # Exact proposal bytes, never a hash/HP/deck/turn projection.
                # This is scheduling deduplication; it proves no state dominance.
                key = self._plan_bytes(artifact)
                if key in self.seen:
                    self.duplicates += 1
                    continue
            except (ContractError, KeyError, TypeError, ValueError):
                self._reject('artifact_contract')
                continue
            request = deepcopy(template_request)
            request['policy_seed'] = source_request.get('policy_seed', request.get('policy_seed', 0))
            request['advisor'] = deepcopy(source_request['advisor'])
            if 'research_progress' in source_request:
                request['research_progress'] = deepcopy(source_request['research_progress'])
            else:
                request.pop('research_progress', None)
            if 'policy_prior' in source_request:
                request['policy_prior'] = deepcopy(source_request['policy_prior'])
            else:
                request.pop('policy_prior', None)
            request['history'] = deepcopy(artifact['entry_history'])
            request['f1_winner_proposal'] = deepcopy(artifact)
            request['capture_f1_winners'] = True
            request['capture_checkpoints'] = False
            for field in ('checkpoint', 'probe', 'card_menu_probe', 'real_card_menu_choice',
                          'expected_evidence', 'stop_at_strategic_decision', 'stop_at_floor'):
                request.pop(field, None)
            self.seen.add(key)
            self.proposals += 1
            specs.append({'kind': 'f1_winner_reuse', 'category': 'failure', 'source': label,
                          'repair': {'source': label, 'member_index': artifact['member_index'],
                                     'entry_prefix_length': len(artifact['entry_history']),
                                     'reuses_computed_f1_actions': True},
                          'request': request})
        return specs

    @staticmethod
    def _reason_code(result):
        metadata = result.get('f1_winner_reuse') or {}
        if isinstance(metadata, dict):
            guard = metadata.get('failed_entry_guard')
            if isinstance(guard, str) and guard:
                return 'CHECKPOINT_MISMATCH:' + guard
            deployment = metadata.get('deployment') or {}
            if isinstance(deployment, dict) and deployment.get('rejection_reason'):
                return str(deployment['rejection_reason'])
        text = str(result.get('reason') or '')
        found = re.search(r'CHECKPOINT_MISMATCH:(f1_winner_[A-Za-z0-9_]+)|F1_WINNER_[A-Z_]+(?::[A-Za-z0-9_:]+)?|(?:NATIVE|QUEUE|MEMORY)_[A-Z_]+', text)
        return found.group(0) if found else (text.splitlines()[0][:160] if text else 'UNKNOWN_WITHOUT_REASON')

    def record_execution(self, result, label, request=None):
        """Allocation diagnostics only; never creates a winner or an exclusion."""
        if not self.enabled or label in self.execution_labels or result.get('synthetic'):
            return
        self.execution_labels.add(label); self.consumers_requested += 1
        status = result.get('status') if isinstance(result.get('status'), str) else 'UNKNOWN'
        self.consumer_statuses[status] += 1
        metadata = result.get('f1_winner_reuse') or {}
        metadata = metadata if isinstance(metadata, dict) else {}
        deployment = metadata.get('deployment') or {}
        deployment = deployment if isinstance(deployment, dict) else {}
        loaded = deployment.get('loaded') is True
        self.guard_attempts += int(metadata.get('guard_attempted') is True)
        self.entry_guards_passed += int(metadata.get('proposal_entry_checked') is True)
        self.loaded_routes += int(loaded)
        # Loading/mapping a forecast is not successful F1 execution. Count only
        # the actual native CombatWon callback for the proposal's encounter.
        self.native_f1_wins += int(loaded and metadata.get('native_f1_won') is True)
        self.native_f2_entries += int(loaded and metadata.get('native_f2_entered') is True)
        proposal = (request or {}).get('f1_winner_proposal')
        metrics = result.get('advisor_metrics') or {}
        searches = metrics.get('searches') if isinstance(metrics, dict) else None
        encounter = proposal.get('encounter') if isinstance(proposal, dict) else None
        if isinstance(searches, list) and isinstance(encounter, str):
            self.f1_search_audits_known += 1
            self.f1_member_search_records += sum(isinstance(row, dict) and row.get('encounter') == encounter for row in searches)
        else:
            self.f1_search_audits_unknown += 1
        value = result.get('value')
        terminal = status == 'TERMINAL' and result.get('reason') is None and isinstance(value, list) and value and type(value[0]) is int and value[0] in (0, 1)
        self.native_win_candidates += int(bool(terminal and value[0] == 1 and result.get('native_terminal_observed') is True))
        if not terminal:
            self.unknown_consumers += 1
            reason = self._reason_code(result)
            self.consumer_reasons[reason] += 1
            if reason.startswith('CHECKPOINT_MISMATCH:f1_winner_') or reason in (
                    'F1_WINNER_ROOT_STATE_MISMATCH', 'F1_WINNER_SCHEMA', 'F1_WINNER_NATIVE_PROGRESS_REQUIRED'):
                self.guard_mismatch_reasons[reason] += 1

    def snapshot(self):
        return {'enabled': self.enabled, 'prefer_predicted_f1_hp': self.prefer_hp,
                'observed_real_f2_deaths': self.observed_f2_deaths,
                'alternative_requests': self.proposals, 'duplicate_plans': self.duplicates,
                'skipped_invalid_artifacts': self.invalid_artifacts,
                'artifact_rejection_reasons': dict(self.artifact_rejections),
                'captured_f1_root_reports': self.captured_f1_roots,
                'captured_predicted_winner_members': self.captured_winner_members,
                'selected_predicted_winner_members': self.selected_winner_members,
                'selected_full_plans_marked_used': self.selected_plans_marked,
                'consumer_evaluations': self.consumers_requested,
                'consumer_statuses': dict(self.consumer_statuses),
                'consumer_guard_attempts': self.guard_attempts,
                'consumer_entry_guards_passed': self.entry_guards_passed,
                'consumer_routes_loaded': self.loaded_routes,
                'consumer_native_f1_wins': self.native_f1_wins,
                'consumer_native_f2_entries': self.native_f2_entries,
                'consumer_native_win_candidates': self.native_win_candidates,
                'consumer_f1_search_audits_known': self.f1_search_audits_known,
                'consumer_f1_search_audits_unknown': self.f1_search_audits_unknown,
                'consumer_f1_member_search_records': self.f1_member_search_records,
                'consumer_unknown': self.unknown_consumers,
                'consumer_unknown_reasons': dict(self.consumer_reasons),
                'consumer_guard_mismatch_reasons': dict(self.guard_mismatch_reasons),
                'additional_f1_searches': 0,
                'scope': 'candidate expansion using complete stored plans; no pruning or win certificate',
                'native_state_equivalence_proven': False}
