"""Separate F1-real + full-HP F2-probe allocation regression.

One real F1 entry supplies one row. Several real entries may share F2 samples;
their sample ownership is reported separately and never described as
independent F2 measurements. Raw losses keep both gates on their own common
life scale. Synthetic outcomes never enter the real GateModels regressions.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math

from .gatemodel import entry_features, _ridge, _support
from .paired_card_probes import valid_outcome
from .probes import scaled


@dataclass
class _Entry:
    features: dict
    f1: dict
    f2: list
    sample_group: bytes | str | None


class F2JointModel:
    """Ridge 3.0, first fit at 24 entries, refresh after 16 changed entries."""

    pairs_fitted = 0
    pair_z = {}
    size_z = 0.0
    probe_z = {}

    def __init__(self):
        self.ridge, self.minimum, self.refresh, self.passes = 3.0, 24, 16, 12
        self.entries = {}
        self.dirty = set()
        self.f1_total = self.f2_total = 0.0
        self.fitted_life_totals = None
        self.fitted = 0
        self.intercept = 0.0
        self.weights, self.z, self.support = {}, {}, {}
        self.residual = None
        self.version = 0
        self.rejections = Counter()

    @staticmethod
    def _features(observation):
        if not isinstance(observation, dict):
            raise ValueError('missing_f1_entry_observation')
        hp, maximum = float(observation['hp']), float(observation['max_hp'])
        if not math.isfinite(hp) or not math.isfinite(maximum) or hp < 0 or maximum <= 0:
            raise ValueError('invalid_f1_entry_hp')
        if not isinstance(observation.get('deck'), list) or not isinstance(observation.get('relics'), list):
            raise ValueError('missing_f1_entry_items')
        features = entry_features(observation)
        if not all(math.isfinite(value) for value in features.values()):
            raise ValueError('invalid_f1_entry_features')
        return features

    @staticmethod
    def _f1(raw):
        if not isinstance(raw, dict):
            raise ValueError('missing_real_f1_outcome')
        loss = raw.get('lost')
        if 'won' in raw and (type(raw['won']) is not bool or raw['won'] != (loss is None)):
            raise ValueError('inconsistent_real_f1_outcome')
        if loss is None:
            value = raw.get('outcome')
            if type(value) not in (int, float) or not math.isfinite(value) or value < 1:
                raise ValueError('invalid_real_f1_pass')
            return {'outcome': float(value), 'lost': None}
        if (not isinstance(loss, (list, tuple)) or len(loss) != 2 or
                any(type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in loss) or
                loss[1] <= 0 or loss[0] > loss[1]):
            raise ValueError('unknown_real_f1_loss')
        return {'outcome': None, 'lost': (float(loss[0]), float(loss[1]))}

    @staticmethod
    def _better_f1(candidate, known):
        if candidate['lost'] is None:
            return known['lost'] is not None or candidate['outcome'] > known['outcome']
        return known['lost'] is not None and candidate['lost'][0] > known['lost'][0]

    def set_life_totals(self, *, f1_life_total=None, f2_life_total=None):
        """Accept all observed gate totals, including unsampled real F1 entries.

        A larger denominator changes every affected target, so those rows are
        dirty. Missing totals never become zero-loss evidence.
        """
        changed = False
        for name, value in (('f1_total', f1_life_total), ('f2_total', f2_life_total)):
            if type(value) in (int, float) and math.isfinite(value) and value > getattr(self, name):
                setattr(self, name, float(value))
                changed = True
        if changed:
            self.dirty.update(self.entries)
        self._maybe_fit()
        return changed

    def observe(self, entry_key, observation, f1_raw, f2_outcomes, *,
                f1_life_total=None, f2_life_total=None, sample_group_key=None):
        """Add/update one real F1 entry, reporting shared probe ownership.

        A complete five- or ten-sample table is required. Incomplete/illegal
        samples stay out of this regression rather than being scored as zero.
        """
        if not isinstance(entry_key, bytes) or not entry_key:
            self.rejections['missing_exact_entry_key'] += 1
            return False
        if (not isinstance(f2_outcomes, (list, tuple)) or len(f2_outcomes) not in (5, 10) or
                not all(valid_outcome(row) for row in f2_outcomes)):
            self.rejections['unknown_or_incomplete_f2_samples'] += 1
            return False
        try:
            features, f1 = self._features(observation), self._f1(f1_raw)
        except (TypeError, ValueError, KeyError):
            self.rejections['unknown_or_invalid_real_f1_entry'] += 1
            return False
        known = self.entries.get(entry_key)
        # Statistical values contain finite floats; native canonical() is an
        # action contract and intentionally rejects them. Normalise only the
        # measured outcome fields and compare them directly, without rounding.
        outcomes = [{'won': row['won'],
                     'outcome': float(row['outcome']) if row['won'] else None,
                     'lost': None if row['won'] else tuple(float(value) for value in row['lost'])}
                    for row in f2_outcomes]
        sample_group = sample_group_key if isinstance(sample_group_key, (bytes, str)) else None
        if known is not None:
            if features != known.features:
                self.rejections['same_key_different_features'] += 1
                return False
            if len(outcomes) < len(known.f2) or outcomes[:len(known.f2)] != known.f2:
                self.rejections['same_key_different_or_shorter_samples'] += 1
                return False
            if not self._better_f1(f1, known.f1):
                f1 = known.f1
            if f1 == known.f1 and outcomes == known.f2:
                if sample_group is not None:
                    known.sample_group = sample_group
                self.set_life_totals(f1_life_total=f1_life_total, f2_life_total=f2_life_total)
                return False
        self.entries[entry_key] = _Entry(features, f1, outcomes, sample_group)
        self.dirty.add(entry_key)
        f1_observed = f1['lost'][1] if f1['lost'] is not None else 0.0
        f2_observed = max((row['lost'][1] for row in outcomes if not row['won']), default=0.0)
        first_total = f1_life_total if type(f1_life_total) in (int, float) and math.isfinite(f1_life_total) else 0.0
        second_total = f2_life_total if type(f2_life_total) in (int, float) and math.isfinite(f2_life_total) else 0.0
        self.set_life_totals(f1_life_total=max(f1_observed, first_total),
                             f2_life_total=max(f2_observed, second_total))
        self._maybe_fit()
        return True

    def target(self, entry_key):
        row = self.entries.get(entry_key)
        if row is None:
            return None
        first = row.f1['outcome'] if row.f1['lost'] is None else min(1.0, row.f1['lost'][0] / self.f1_total)
        second = sum(scaled(outcome, self.f2_total) for outcome in row.f2) / len(row.f2)
        return first + second

    def _maybe_fit(self):
        if len(self.entries) < self.minimum or (self.fitted and len(self.dirty) < self.refresh):
            return
        rows = [(row.features, self.target(key)) for key, row in self.entries.items()]
        self.intercept, self.weights, self.z, self.residual = _ridge(rows, self.ridge, self.passes, self.weights)
        self.support = _support(rows)
        self.fitted = len(rows)
        self.fitted_life_totals = {'f1': self.f1_total, 'f2': self.f2_total}
        self.dirty.clear()
        self.version += 1

    def predict(self, observation):
        if not self.fitted:
            return None
        try:
            features = self._features(observation)
        except (TypeError, ValueError, KeyError):
            return None
        return self.intercept + sum(value * self.weights.get(key, 0.0) for key, value in features.items())

    def snapshot(self, limit=12):
        groups = {row.sample_group for row in self.entries.values()}
        ranked = sorted(((key, value) for key, value in self.z.items() if not key.startswith('#')),
                        key=lambda item: (-item[1], item[0]))
        return {'entries': len(self.entries), 'fitted_entries': self.fitted, 'dirty_entries': len(self.dirty),
                'entry_count_is': 'distinct real F1 states; shared F2 samples are correlated',
                'sample_groups': None if None in groups else len(groups),
                'known_sample_groups': len(groups - {None}),
                'unknown_sample_group_rows': sum(row.sample_group is None for row in self.entries.values()),
                'ridge': self.ridge, 'minimum_entries': self.minimum, 'refresh_entries': self.refresh,
                'f1_life_total': self.f1_total, 'f2_life_total': self.f2_total,
                'fitted_life_totals': self.fitted_life_totals, 'residual_spread': self.residual,
                'version': self.version, 'rejections': dict(self.rejections),
                'top': [[key, round(value, 2)] for key, value in ranked[:limit]],
                'bottom': [[key, round(value, 2)] for key, value in ranked[-limit:][::-1]],
                'target': 'real F1 outcome + full-HP synthetic F2 sample mean, separate common life totals',
                'scope': 'separate joint allocation regression; never a real gate row, bound, cut or proof'}
