"""i075 preparation candidates. All signals allocate work; none exclude runs.

Only native known map nodes/edges are used. Shortest/least-elite paths below
are proposals, not abstractions or bounds on what a real room will permit.
Native consumers must recheck the full source and each actual move.
"""
from collections import Counter, deque
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import heapq
from ..canonical import canonical, ContractError

SWITCHES = ('gold_shop_routes', 'low_hp_routes', 'low_hp_routes_any_act', 'lean_third_act',
            'shop_preparation', 'resource_telemetry')
ROUTE_KINDS = ('macro_shop_route', 'macro_low_hp_route', 'macro_route_portfolio')


def enabled(config):
    # any-act is a modifier of low_hp_routes, not a separate route generator.
    return any(getattr(config, key, False) for key in SWITCHES if key != 'low_hp_routes_any_act') or getattr(config,'macro_routes','off')!='off'


def captures_graph(config):
    return bool(config.gold_shop_routes or config.low_hp_routes or getattr(config,'macro_routes','off')!='off')


def number(value):
    if type(value) not in (int, str):
        return None
    try:
        value = Decimal(value)
        return value if value.is_finite() else None
    except InvalidOperation:
        return None


def coord(value):
    if not isinstance(value, dict) or any(type(value.get(k)) is not int for k in ('col', 'row')):
        raise ValueError('invalid_map_coordinate')
    return value['col'], value['row']


def _decimal_menu_value(value):
    """Exact decimal representation without context-dependent rounding."""
    sign, digits, exponent = value.as_tuple()
    digits = list(digits)
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop()
        exponent += 1
    if not any(digits):
        sign, digits, exponent = 0, [0], 0
    return {'sign': sign, 'digits': digits, 'exponent': exponent}


def forge_menu_key(evidence, hp_mode='exact', *, map_coord=None, hp_percent=50):
    """Pure heuristic menu grouping key, or None when required data is unknown.

    Cards are an ID/upgrade multiset; relic order, potion slots and duplicate
    entries are retained. threshold replaces HP with the user rule's predicate
    while retaining max HP; exact retains both numbers. Neither mode includes
    every game effect, RNG or hidden counter, so this is never a state/cache/proof key.
    Callers must keep the original search branches and replay any proposal.
    """
    if hp_mode not in ('exact', 'threshold'):
        raise ValueError('invalid_forge_menu_dedup')
    if type(hp_percent) is not int or not 1 <= hp_percent <= 100:
        raise ValueError('invalid_forge_menu_hp_percent')
    if not isinstance(evidence, dict) or not isinstance(evidence.get('observation'), dict):
        return None
    obs = evidence['observation']
    phase = evidence.get('phase')
    if (not isinstance(phase, str) or not phase
            or any(type(obs.get(key)) is not int or obs[key] < 0 for key in ('act', 'floor'))):
        return None
    try:
        supplied = [obs[key] for key in ('map_coord', 'current_coord') if key in obs]
        if map_coord is not None:
            supplied.append(map_coord)
        if not supplied:
            return None
        coordinates = [coord(value) for value in supplied]
        if any(value != coordinates[0] for value in coordinates):
            return None
        deck, relics, potions = (obs.get(key) for key in ('deck', 'relics', 'potions'))
        if (not isinstance(deck, list) or not isinstance(relics, list) or not isinstance(potions, list)
                or any(not isinstance(card, dict) or not isinstance(card.get('id'), str) or not card['id']
                       or type(card.get('upgrade')) is not int or card['upgrade'] < 0 for card in deck)
                or any(not isinstance(relic, str) or not relic for relic in relics)
                or any(potion is not None and (not isinstance(potion, str) or not potion) for potion in potions)):
            return None
        hp, maximum = number(obs.get('hp')), number(obs.get('max_hp'))
        if hp is None or maximum is None or hp < 0 or maximum <= 0:
            return None
        if hp_mode == 'exact':
            health = {'hp': _decimal_menu_value(hp), 'max_hp': _decimal_menu_value(maximum)}
        else:
            # Use integer ratios so even boundary decimals are never rounded.
            hp_n, hp_d = hp.as_integer_ratio()
            max_n, max_d = maximum.as_integer_ratio()
            health = {'hp_percent': hp_percent,
                      'at_or_above': 100 * hp_n * max_d >= hp_percent * max_n * hp_d,
                      'max_hp': _decimal_menu_value(maximum)}
        return canonical({'schema': 'spire-forge-menu-group/v1', 'hp_mode': hp_mode,
                          'act': obs['act'], 'floor': obs['floor'], 'phase': phase,
                          'map_coord': {'col': coordinates[0][0], 'row': coordinates[0][1]},
                          'deck': sorted([[card['id'], card['upgrade']] for card in deck]),
                          'relics': relics, 'potions': potions, 'health': health})
    except (ContractError, ValueError, TypeError, KeyError):
        return None


def _observed_menu_coord(result, index, evidence):
    """Bind an opt-in native side-table entry to the actual decision evidence.

    Never infer live coordinates from a floor, a previous graph or old actions.
    Legacy results may lack this metadata; that leaves grouping unresolved.
    """
    sources = result.get('preparation_menu_sources')
    if not isinstance(sources, list):
        return None
    rows = [row for row in sources if isinstance(row, dict)
            and type(row.get('index')) is int and row['index'] == index]
    if len(rows) != 1:
        return None
    row = rows[0]
    try:
        if (row.get('phase') != evidence.get('phase')
                or not isinstance(row.get('observation'), dict)
                or canonical(row['observation']) != canonical(evidence.get('observation'))):
            return None
        coord(row.get('map_coord'))
        return row['map_coord']
    except (ContractError, ValueError, TypeError, KeyError):
        return None


class NativeMap:
    """Finite native graph. Cycles are legal; only candidate paths are simple.

    Positive hop costs make Dijkstra terminate even for a cyclic map. This is
    never used to prune a game loop or to declare any route impossible.
    """
    def __init__(self, graph, actions):
        if not isinstance(graph, dict) or graph.get('schema') != 'spire-current-act-map/v1':
            raise ValueError('map_schema')
        self.act = graph.get('act')
        if type(self.act) is not int or self.act < 0:
            raise ValueError('map_act')
        self.nodes = {}
        for node in graph.get('nodes', []):
            key = coord(node)
            if key in self.nodes or not isinstance(node.get('type'), str):
                raise ValueError('duplicate_or_invalid_map_node')
            self.nodes[key] = node['type']
        if not self.nodes:
            raise ValueError('empty_map')
        self.edges = {key: set() for key in self.nodes}
        for edge in graph.get('edges', []):
            a, b = coord(edge['from']), coord(edge['to'])
            if a not in self.nodes or b not in self.nodes:
                raise ValueError('dangling_map_edge')
            self.edges[a].add(b)
        self.starts = sorted({coord(a) for a in actions if a.get('kind') == 'map'})
        if not self.starts or any(key not in self.nodes for key in self.starts):
            raise ValueError('source_menu_not_in_map')
        # First moves come from the actual menu, including native special start
        # behavior. Do not assume coordinates alone determine move legality.
        self.boss = coord(graph['boss_coord']) if graph.get('boss_coord') is not None else None
        if self.boss is not None and self.boss not in self.nodes:
            raise ValueError('missing_first_boss_node')

    def distances(self, starts=None):
        distances = {key: 1 for key in (self.starts if starts is None else starts)}
        pending = deque(sorted(distances))
        while pending:
            key = pending.popleft()
            for child in sorted(self.edges[key]):
                if child not in distances:
                    distances[child] = distances[key] + 1
                    pending.append(child)
        return distances

    def path(self, target, starts=None):
        # (elite count, hops, complete coordinate sequence) deterministic ties.
        pending = []
        best = {}
        for key in (self.starts if starts is None else starts):
            score = (int(self.nodes[key] == 'Elite'), 1, (key,))
            best[key] = score
            heapq.heappush(pending, (*score, key))
        while pending:
            elites, hops, path, key = heapq.heappop(pending)
            if best[key] != (elites, hops, path):
                continue
            if key == target:
                return list(path)
            for child in sorted(self.edges[key]):
                score = (elites + int(self.nodes[child] == 'Elite'), hops + 1, path + (child,))
                if child not in best or score < best[child]:
                    best[child] = score
                    heapq.heappush(pending, (*score, child))
        return None

    def shops(self):
        distances = self.distances()
        return sorted((key for key in distances if self.nodes[key] == 'Shop'),
                      key=lambda key: (distances[key], key))


class PreparationCandidates:
    def __init__(self, config):
        self.config = config
        self.enabled = enabled(config)
        self.seen = set()
        self.forge_menus = set()
        self.forge_menu_dedup = getattr(config, 'forge_menu_dedup', 'exact')
        if self.forge_menu_dedup not in ('exact', 'threshold'):
            raise ValueError('invalid_forge_menu_dedup')
        self.source_labels = set()
        self.executed_labels = set()
        self.counts = Counter()
        self.rejections = Counter()
        self.outcomes = Counter()

    @staticmethod
    def context(template):
        return {key: template[key] for key in ('seed', 'character', 'ascension', 'unlocks')} | {
            'information': 'full', 'objective': 'whole_run_victory/v1'}

    def validate_source(self, source, result, template):
        if not isinstance(source, dict) or source.get('schema') != 'spire-map-decision-source/v1':
            raise ValueError('source_schema')
        if source.get('phase') != 'map':
            raise ValueError('not_map_source')
        history = source.get('entry_history')
        index = source.get('index')
        if not isinstance(history, list) or type(index) is not int or index != len(history):
            raise ValueError('source_index')
        trace = result.get('trace', [])
        evidence = result.get('decision_evidence', [])
        if index >= len(trace) or index >= len(evidence) or canonical(history) != canonical(trace[:index]):
            raise ValueError('source_prefix')
        if canonical(source.get('context')) != canonical(self.context(template)):
            raise ValueError('source_context')
        baseline = template.get('research_progress') or {}
        if not baseline.get('native_identity') or canonical(source.get('native_identity')) != canonical(baseline['native_identity']):
            raise ValueError('source_native_identity')
        if not baseline.get('baseline_sha256') or source.get('baseline_sha256') != baseline['baseline_sha256']:
            raise ValueError('source_progress_baseline')
        obs, actions = source.get('entry_observation'), source.get('available_actions')
        if (not isinstance(obs, dict) or not isinstance(actions, list) or not actions
                or evidence[index].get('phase') != 'map'
                or canonical(obs) != canonical(evidence[index].get('observation'))
                or canonical(actions) != canonical(evidence[index].get('available_actions'))):
            raise ValueError('source_menu_or_observation')
        state = source.get('source_native_state')
        if (not isinstance(state, dict) or not isinstance(state.get('run'), dict)
                or not isinstance(state['run'].get('native_json'), str) or not state['run']['native_json']
                or not isinstance(state.get('progress'), dict) or not isinstance(state.get('action_runtime'), dict)):
            raise ValueError('source_native_state')
        graph = NativeMap(source.get('graph'), actions)
        if obs.get('act') != graph.act:
            raise ValueError('source_map_act')
        return graph, obs

    @staticmethod
    def template_request(template, source_request):
        request = deepcopy(template)
        for key in ('policy_seed', 'policy_prior', 'advisor'):
            if key in source_request:
                request[key] = deepcopy(source_request[key])
            else:
                request.pop(key, None)
        # Never nest a research consumer or synthetic edit in another request.
        for key in ('checkpoint', 'f1_winner_proposal', 'real_card_menu_choice',
                    'probe', 'card_menu_probe', 'map_route_plan', 'stop_at_floor',
                    'stop_at_strategic_decision'):
            request.pop(key, None)
        return request

    def route_spec(self, source, graph, path, shops, kind, template, source_request, label, family):
        if not path:
            return None
        moves = [{'act': graph.act, 'col': c, 'row': r} for c, r in path]
        targets = [{'act': graph.act, 'col': c, 'row': r} for c, r in shops]
        plan = {'schema': 'spire-map-route-plan/v1', 'source': deepcopy(source),
                'moves': moves, 'target_shops': targets}
        if targets:
            # The user keeps one successful removal at every target shop. The
            # flag controls the additional purchases, never that removal stage.
            plan['shop_policy'] = ('potions_remove_relic/v1' if self.config.shop_preparation
                                   else 'removal-only/v1')
        key = canonical({'plan': plan, 'policy_seed': source_request.get('policy_seed'),
                         'policy_prior': source_request.get('policy_prior'), 'advisor': source_request.get('advisor'),
                         'research_progress': template.get('research_progress')})
        if key in self.seen:
            self.counts['duplicate_proposals'] += 1
            return None
        self.seen.add(key)
        request = self.template_request(template, source_request)
        request.update(history=deepcopy(source['entry_history']), capture_checkpoints=False, map_route_plan=plan)
        self.counts[kind] += 1
        return {'kind': kind, 'category': 'preparation', 'family': family,
                'preparation': {'source': label, 'index': source['index'], 'act': graph.act,
                                'allocation_only': True, 'target_shops': targets}, 'request': request}

    def observe(self, result, label, template, source_request, *, family=None, kind=None):
        if not self.enabled or result.get('synthetic') or label in self.source_labels:
            return []
        self.source_labels.add(label)
        if not template.get('advisor') or source_request.get('map_route_plan'):
            return []  # No recursive route proposal generation.
        try:
            if (canonical(self.context(template)) != canonical(self.context(source_request))
                    or canonical(template.get('research_progress')) != canonical(source_request.get('research_progress'))):
                self.rejections['source_request_contract'] += 1
                return []
        except (ContractError, KeyError, TypeError, ValueError):
            self.rejections['source_request_contract'] += 1
            return []
        proposals = []
        used = set()
        if captures_graph(self.config):
            for source in result.get('map_decision_sources') or []:
                if isinstance(source, dict) and source.get('phase') == 'shop':
                    continue
                try:
                    graph, obs = self.validate_source(source, result, template)
                    gold, hp, maximum = number(obs.get('gold')), number(obs.get('hp')), number(obs.get('max_hp'))
                    if self.config.gold_shop_routes and gold is not None and gold >= self.config.gold_shop_threshold and ('shop', graph.act) not in used:
                        shops = graph.shops()
                        if shops:
                            first = shops[0]
                            path = graph.path(first)
                            spec = self.route_spec(source, graph, path, [first], ROUTE_KINDS[0], template, source_request, label, family)
                            if spec:
                                proposals.append(spec)
                            used.add(('shop', graph.act))
                            if gold >= 2 * self.config.gold_shop_threshold and len(proposals) < self.config.preparation_limit:
                                after = sorted(graph.edges[first])
                                distances = graph.distances(after)
                                second = sorted((key for key in distances if graph.nodes[key] == 'Shop' and key not in path),
                                                key=lambda key: (distances[key], key))
                                if second:
                                    suffix = graph.path(second[0], after)
                                    spec = self.route_spec(source, graph, path + suffix, [first, second[0]], ROUTE_KINDS[0], template, source_request, label, family)
                                    if spec:
                                        proposals.append(spec)
                    if (len(proposals) < self.config.preparation_limit and self.config.low_hp_routes
                            and (graph.act == 2 or getattr(self.config, 'low_hp_routes_any_act', False))
                            and hp is not None and maximum is not None
                            and maximum > 0 and hp > 0 and 100 * hp < self.config.low_hp_percent * maximum
                            and ('hp', graph.act) not in used and graph.boss is not None):
                        path = graph.path(graph.boss)
                        spec = self.route_spec(source, graph, path, [], ROUTE_KINDS[1], template, source_request, label, family)
                        if spec:
                            proposals.append(spec)
                        used.add(('hp', graph.act))
                except (ContractError, KeyError, TypeError, ValueError) as error:
                    self.rejections[str(error)] += 1
                if len(proposals) >= self.config.preparation_limit:
                    break
        if self.config.lean_third_act and kind != 'macro_forge':
            trace = result.get('trace', [])
            # Only actual SMITH alternatives remain. Grouping schedules fewer
            # new proposals; it never deletes ordinary search-space branches.
            for index in reversed(range(min(len(trace), len(result.get('decision_evidence', []))))):
                if len(proposals) >= self.config.preparation_limit:
                    break
                e = result['decision_evidence'][index]
                obs = e.get('observation') or {}
                if obs.get('act') != 2:
                    continue
                hp, maximum = number(obs.get('hp')), number(obs.get('max_hp'))
                for action in e.get('available_actions', []):
                    if not (e.get('phase') == 'rest' and action.get('kind') == 'rest'
                            and action.get('option') == 'SMITH' and hp is not None and maximum is not None
                            and maximum > 0 and 100 * hp >= self.config.low_hp_percent * maximum):
                        continue
                    if canonical(action) == canonical(trace[index]):
                        continue
                    prefix = trace[:index] + [action]
                    request = self.template_request(template, source_request)
                    request['history'] = deepcopy(prefix)
                    key = canonical(request)
                    if key in self.seen:
                        self.counts['duplicate_proposals'] += 1
                        continue
                    menu_key = forge_menu_key(e, self.forge_menu_dedup,
                                              map_coord=_observed_menu_coord(result, index, e),
                                              hp_percent=self.config.low_hp_percent)
                    if menu_key is None:
                        self.counts['forge_menu_key_missing'] += 1
                    elif menu_key in self.forge_menus:
                        self.counts['duplicate_proposals'] += 1
                        self.counts['forge_menu_duplicates'] += 1
                        continue
                    self.seen.add(key)
                    if menu_key is not None:
                        self.forge_menus.add(menu_key)
                    proposals.append({'kind': 'macro_forge', 'category': 'preparation', 'family': family,
                                      'preparation': {'source': label, 'index': index, 'act': 2, 'allocation_only': True},
                                      'request': request})
                    self.counts['macro_forge'] += 1
                    break
        self.counts['proposals'] += len(proposals[:self.config.preparation_limit])
        return proposals[:self.config.preparation_limit]

    def record_execution(self, result, label):
        if label in self.executed_labels:
            return
        self.executed_labels.add(label)
        report = result.get('map_route_result')
        self.outcomes['consumers'] += 1
        if not isinstance(report, dict):
            self.outcomes['missing_native_report'] += 1
            return
        self.outcomes['entry_checked'] += int(report.get('entry_checked') is True)
        self.outcomes['complete'] += int(report.get('complete') is True)
        arrivals = report.get('arrived_shops')
        if isinstance(arrivals, list):
            self.outcomes['actual_shop_arrivals'] += sum(isinstance(row, dict) and row.get('native_room') == 'MerchantRoom' for row in arrivals)
        self.outcomes['status_' + str(result.get('status'))] += 1
        if result.get('reason'):
            self.outcomes['reason_' + str(result['reason'])] += 1

    def snapshot(self):
        return {'enabled': self.enabled, 'switches': {key: getattr(self.config, key, False) for key in SWITCHES},
                'gold_threshold': self.config.gold_shop_threshold, 'low_hp_percent': self.config.low_hp_percent,
                'forge_menu_dedup': self.forge_menu_dedup,
                'per_trajectory_limit': self.config.preparation_limit, 'counts': dict(self.counts),
                'source_rejections': dict(self.rejections), 'native_outcomes': dict(self.outcomes),
                'scope': 'candidate allocation only; never an infeasibility proof, cut, bound or optimality claim'}
