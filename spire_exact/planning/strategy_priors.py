"""Version-robust strategy priors over native structural deck features.

Community guidance is encoded only as *directions* and coarse weights over the
capability snapshot the native host derives from game metadata at run time
(StrategicStateEvaluator, schema ``strategic-capability/v1``: DynamicVars,
card type, keywords and IL references). No card, relic, potion, monster or boss
name appears here, so a patch that renames, rebalances or adds content keeps
producing meaningful features without editing this file.

Two kinds of terms:

* count/share features (scaling sources, curses, deck size, upgrade share, HP
  share) have soft absolute targets: they count structure, not balance numbers;
* balance-dependent averages (damage/block/draw per card) are compared only
  within this run's own population of boss entries (z-scores), never against
  absolute numbers that a balance patch could invalidate.

The seed's own native boss outcomes override the prior: ``posterior`` solves a
small ridge regression whose prior mean is the community direction. Every value
here orders allocation only; none is a bound, a pruning rule or a proof.
"""
from __future__ import annotations

import math

from .gatemodel import _invert

FEATURE_SCHEMA = 'strategic-capability/v1'

# Reviewed 2026-10-05 for the pinned game build (0.111.0). Because only native
# structural features are used, a later build keeps working; the review date
# and sources are recorded so a new review can replace the directions.
BUILTIN = {
    'schema': 'spire-strategy-prior/v1',
    'feature_schema': FEATURE_SCHEMA,
    'reviewed': '2026-10-05',
    'game_versions_reviewed': ['0.111.0'],
    'sources': [
        {'title': 'Slay the Spire 2: Bosses (wiki.gg)', 'url': 'https://slaythespire.wiki.gg/wiki/Slay_the_Spire_2:Bosses'},
        {'title': '10 Tips and Tricks for Ascension 10 in Slay the Spire 2 (DualShockers)',
         'url': 'https://www.dualshockers.com/slay-the-spire-2-tips-and-tricks-for-ascension-10/'},
        {'title': 'Slay the Spire 2 Ascension 10 Ironclad guides (community, several)',
         'url': 'https://www.solojugadores.com/en/slay-the-spire-2-ironclad-ascension-10-guide/'},
    ],
    # Principles that agree across the reviewed guides. Disagreements (for
    # example rest versus smith, one versus several elites per act) are left
    # out on purpose: full-information search tests both sides natively.
    'principles': [
        'final-act bosses gain strength over time: the deck needs scaling sources',
        'two final bosses are fought back to back without a rest: HP must last two fights',
        'sustained (non-exhaust) block and card draw/energy keep the engine running',
        'a lean deck without curses draws its key cards; bloat dilutes them',
        'upgrades matter in boss fights',
    ],
    'classes': {
        # A10 double-boss gauntlet of the final act (F1 then F2).
        'final_gauntlet': {
            'weights': {'scaling_total': 1.0, 'hp_fraction': 0.8, 'steady_block_per_draw': 0.6,
                        'draw_per_card': 0.4, 'energy_per_card': 0.3, 'damage_per_draw': 0.3,
                        'upgrade_density': 0.3, 'deck_size': -0.4, 'burden': -0.6},
            # Soft absolute targets exist only for structure counts/shares.
            'targets': {'scaling_total': (2.0, 'min'), 'burden': (0.0, 'max'), 'deck_size': (30.0, 'max'),
                        'upgrade_density': (0.3, 'min'), 'hp_fraction': (0.6, 'min')},
        },
        'act_boss': {
            'weights': {'damage_per_draw': 0.6, 'hp_fraction': 0.6, 'steady_block_per_draw': 0.5,
                        'scaling_total': 0.4, 'upgrade_density': 0.3, 'draw_per_card': 0.2,
                        'deck_size': -0.2, 'burden': -0.5},
            'targets': {'scaling_total': (1.0, 'min'), 'burden': (0.0, 'max'), 'hp_fraction': (0.5, 'min')},
        },
    },
}

COUNT_FEATURES = frozenset(('scaling_total', 'burden', 'deck_size', 'upgrade_density', 'hp_fraction'))
STRUCTURAL = ('scaling_total', 'steady_block_per_draw', 'draw_per_card', 'energy_per_card', 'damage_per_draw',
              'upgrade_density', 'deck_size', 'burden')


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def features(observation) -> dict:
    """Structural features of one native observation; missing terms are absent.

    A different or missing snapshot schema disables the deck terms instead of
    guessing; HP share still comes from the observation itself.
    """
    if not isinstance(observation, dict):
        return {}
    result = {}
    hp, maximum = _number(observation.get('hp')), _number(observation.get('max_hp'))
    if hp is not None and maximum and maximum > 0:
        result['hp_fraction'] = max(0.0, min(1.0, hp / maximum))
    snapshot = observation.get('strategic')
    if not isinstance(snapshot, dict) or snapshot.get('schema') != FEATURE_SCHEMA:
        return result
    for name in ('damage_per_draw', 'draw_per_card', 'energy_per_card', 'upgrade_density', 'deck_size', 'burden'):
        value = _number(snapshot.get(name))
        if value is not None:
            result[name] = value
    block = _number(snapshot.get('steady_block_per_draw'))
    if block is None:
        block = _number(snapshot.get('block_per_draw'))
    if block is not None:
        result['steady_block_per_draw'] = block
    scaling, strength = _number(snapshot.get('scaling')), _number(snapshot.get('strength'))
    if scaling is not None or strength is not None:
        result['scaling_total'] = (scaling or 0.0) + (strength or 0.0)
    return result


def gate_class(gate, final_act=2) -> str:
    return 'final_gauntlet' if gate and gate[0] == final_act else 'act_boss'


class StructuralPrior:
    """Community directions plus this run's posterior over structural features."""

    def __init__(self, table=None, *, strength: float = 6.0, minimum: int = 8):
        table = BUILTIN if table is None else table
        if not isinstance(table, dict) or table.get('schema') != 'spire-strategy-prior/v1':
            raise ValueError('unknown strategy prior schema')
        if table.get('feature_schema') != FEATURE_SCHEMA:
            raise ValueError('strategy prior targets a different native feature schema')
        classes = table.get('classes')
        if not isinstance(classes, dict) or not classes:
            raise ValueError('strategy prior needs gate classes')
        for name, row in classes.items():
            weights = row.get('weights') if isinstance(row, dict) else None
            if not isinstance(weights, dict) or not weights or any(
                    type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 4 for v in weights.values()):
                raise ValueError('strategy prior weights must be finite and within -4..4: ' + str(name))
        if type(minimum) is not int or minimum < 3 or not math.isfinite(strength) or strength <= 0:
            raise ValueError('invalid strategy prior posterior settings')
        self.table, self.strength, self.minimum = table, float(strength), minimum
        self.fits = {}

    def weights(self, cls: str) -> dict:
        return dict(self.table['classes'].get(cls, {}).get('weights', {}))

    def targets(self, cls: str) -> dict:
        return dict(self.table['classes'].get(cls, {}).get('targets', {}))

    # ---- population statistics ------------------------------------------
    @staticmethod
    def moments(rows) -> dict:
        """feature -> (mean, standard deviation) over feature dicts."""
        values = {}
        for row in rows:
            for key, value in row.items():
                values.setdefault(key, []).append(value)
        stats = {}
        for key, column in values.items():
            mean = sum(column) / len(column)
            spread = math.sqrt(sum((v - mean) ** 2 for v in column) / max(1, len(column) - 1)) if len(column) > 1 else 0.0
            stats[key] = (mean, spread)
        return stats

    # ---- posterior --------------------------------------------------------
    def posterior(self, cls: str, rows) -> dict:
        """Ridge regression of outcome on standardised features with the prior
        direction as mean. rows = [(features, outcome)], real entries only.
        Returns weights in standardised units; the prior alone below minimum."""
        prior = self.weights(cls)
        usable = [(f, y) for f, y in rows if isinstance(f, dict) and _number(y) is not None]
        if len(usable) < self.minimum:
            self.fits[cls] = {'rows': len(usable), 'fitted': False}
            return prior
        stats = self.moments([f for f, _ in usable])
        keys = sorted(k for k in prior if k in stats and stats[k][1] > 1e-9
                      and sum(k in f for f, _ in usable) == len(usable))
        if not keys:
            self.fits[cls] = {'rows': len(usable), 'fitted': False}
            return prior
        ys = [float(y) for _, y in usable]
        ymean = sum(ys) / len(ys)
        yspread = math.sqrt(sum((y - ymean) ** 2 for y in ys) / max(1, len(ys) - 1)) or 1.0
        # A prior weight of 1 means one standard deviation of the feature moves
        # the outcome by 0.3 of its own spread; the regression works in that unit.
        scale = 0.3 * yspread
        x = [[(f[k] - stats[k][0]) / stats[k][1] for k in keys] for f, _ in usable]
        normal = [[sum(r[i] * r[j] for r in x) + (self.strength if i == j else 0.0) for j in range(len(keys))]
                  for i in range(len(keys))]
        moment = [sum(r[i] * (y - ymean) for r, y in zip(x, ys)) + self.strength * prior[k] * scale
                  for i, k in enumerate(keys)]
        inverse = _invert(normal)
        if inverse is None:
            self.fits[cls] = {'rows': len(usable), 'fitted': False}
            return prior
        fitted = {k: sum(inverse[i][j] * moment[j] for j in range(len(keys))) / scale for i, k in enumerate(keys)}
        merged = dict(prior)
        merged.update({k: max(-4.0, min(4.0, v)) for k, v in fitted.items()})
        self.fits[cls] = {'rows': len(usable), 'fitted': True,
                          'weights': {k: round(v, 3) for k, v in sorted(merged.items())}}
        return merged

    # ---- deficits ---------------------------------------------------------
    def deficit(self, entry: dict, cls: str, population, weights=None) -> tuple:
        """(score in 0..1, [(feature, shortfall)]) for one entry's features.

        Count features use soft absolute targets; balance-dependent averages
        use the run's own population (at least `minimum` entries). Features
        the current weights consider harmless (weight 0 or wrong sign) never
        count as a deficit.
        """
        weights = self.weights(cls) if weights is None else weights
        targets = self.targets(cls)
        stats = self.moments(population) if len(population) >= self.minimum else {}
        total, shortfall, rows = 0.0, 0.0, []
        for key, weight in weights.items():
            if key not in entry or not weight:
                continue
            value = entry[key]
            gap = None
            if key in COUNT_FEATURES and key in targets:
                target, direction = targets[key]
                if direction == 'min' and weight > 0:
                    gap = max(0.0, min(1.0, (target - value) / max(1e-9, abs(target))))
                elif direction == 'max' and weight < 0:
                    gap = max(0.0, min(1.0, (value - target) / max(1.0, abs(target))))
            elif key in stats and stats[key][1] > 1e-9:
                z = (value - stats[key][0]) / stats[key][1]
                gap = max(0.0, min(1.0, -math.copysign(1.0, weight) * z / 2.0))
            if gap is None:
                continue
            total += abs(weight)
            shortfall += abs(weight) * gap
            if gap > 0:
                rows.append((key, round(gap, 3)))
        return (shortfall / total if total else 0.0), rows

    # ---- option effects ---------------------------------------------------
    def option_score(self, delta: dict, cls: str, deficits=(), population=(), weights=None) -> float:
        """How much a learned structural change moves an entry along the
        current weights; deficit features count double. Unit-free."""
        weights = self.weights(cls) if weights is None else weights
        stats = self.moments(population) if len(population) >= self.minimum else {}
        short = {key for key, _ in deficits}
        score = 0.0
        for key, change in delta.items():
            weight = weights.get(key)
            if not weight:
                continue
            unit = 1.0 if key in COUNT_FEATURES else (stats.get(key, (0.0, 0.0))[1] or None)
            if unit is None:
                continue
            score += weight * change / unit * (2.0 if key in short else 1.0)
        return max(-4.0, min(4.0, score))

    def snapshot(self) -> dict:
        return {'schema': self.table.get('schema'), 'feature_schema': self.table.get('feature_schema'),
                'reviewed': self.table.get('reviewed'), 'game_versions_reviewed': self.table.get('game_versions_reviewed'),
                'sources': [row.get('url') for row in self.table.get('sources', [])],
                'posterior': self.fits, 'prior_strength': self.strength, 'minimum_rows': self.minimum,
                'scope': 'structural directions only; allocation order, never a bound, pruning rule or proof'}


class LabelEffects:
    """Running mean structural change after choosing an option label.

    Learned from this run's own consecutive native observations, so a game
    update that changes what a card does changes the learned effect too.
    Only structural deck features are tracked; HP is excluded because fights
    between two observations would contaminate it.
    """

    def __init__(self, limit: int = 4096):
        self.limit = limit
        self.rows = {}

    def observe(self, result: dict) -> int:
        trace = result.get('trace') or []
        evidence = (result.get('decision_evidence') or [])[:len(trace)]
        learned = 0
        for index, row in enumerate(evidence[:-1]):
            labels = row.get('option_labels')
            actions = row.get('available_actions') or []
            if not labels or not actions:
                continue
            chosen = None
            for action, names in zip(actions, labels):
                if action == trace[index]:
                    chosen = names
                    break
            if not chosen or len(chosen) != 1:
                continue
            label = chosen[0]
            before = features(row.get('observation'))
            after = features(evidence[index + 1].get('observation'))
            delta = {k: after[k] - before[k] for k in STRUCTURAL if k in before and k in after}
            if not delta:
                continue
            known = self.rows.get(label)
            if known is None:
                if len(self.rows) >= self.limit:
                    continue
                known = self.rows[label] = [0, {}]
            known[0] += 1
            for key, value in delta.items():
                known[1][key] = known[1].get(key, 0.0) + (value - known[1].get(key, 0.0)) / known[0]
            learned += 1
        return learned

    def effect(self, label: str):
        row = self.rows.get(label)
        return dict(row[1]) if row else None

    def best(self, labels, score):
        """Highest score among an option's labels; None when none was learned."""
        values = [score(self.rows[name][1]) for name in labels if name in self.rows]
        return max(values) if values else None

    def snapshot(self) -> dict:
        return {'labels_learned': len(self.rows), 'limit': self.limit,
                'scope': 'mean structural change after a chosen label in this run; allocation order only'}
