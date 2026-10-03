"""Offline comparison of the gate model's ridge regression with other model families (no native execution).

Same replay, training table, refit moments and metrics as tools/gatemodel_nn_bench.py; only the fitted
models differ (fixed in experiments/iteration-048/plan.md):

  ridge*        the exact ridge solution (the shipped fit is 12 warm-started coordinate passes);
  ridge+shape   exact ridge with squares of the four numeric features, HP x deck size, upgrade and relic totals;
  hurdle        logistic pass probability x ridge on passed entries + (1 - p) x ridge on lost entries;
  ridge+knn     ridge plus the shrunk mean residual of the 8 nearest entries (L1 on card / upgrade / relic counts);
  gbdt          100 depth-2 boosted trees on count thresholds and deciles of the numeric features.

    python tools/gatemodel_alt_bench.py run --seed-dir <run>/seed-<seed> --cache <file.pkl> --out <report.json>
    python tools/gatemodel_alt_bench.py report --out summary.json <report.json> ...

Predictions are allocation signals. Nothing here is a bound or a claim about any state.
"""
import gatemodel_nn_bench as nn
import numpy as np
from spire_exact.planning.gatemodel import _ridge

RIDGE, PASSES, MINIMUM = nn.RIDGE, nn.PASSES, nn.MINIMUM
NEIGHBOURS, TREES, RATE, LEAF = 8, 100, 0.1, 5
NUMERIC = ('#hp', '#deck', '#maxhp', '#potions')


def shaped(features):
    out = dict(features); hp, deck, cap, potions = (features.get(key, 0.0) for key in NUMERIC)
    out.update({'#hp^2': hp * hp, '#deck^2': deck * deck, '#maxhp^2': cap * cap, '#potions^2': potions * potions, '#hp*deck': hp * deck,
                '#ups': sum(v for k, v in features.items() if k.startswith('up:')) / 10.0,
                '#relics': sum(v for k, v in features.items() if k.startswith('relic:')) / 10.0})
    return out


def design(index, features_list):
    X = np.zeros((len(features_list), len(index)), np.float64)
    for i, features in enumerate(features_list):
        for key, value in features.items():
            j = index.get(key)
            if j is not None: X[i, j] = value
    return X


def solve(X, y, weights=None):
    """Exact ridge with a free intercept: (intercept at the column means, weights, column means)."""
    s = np.ones(len(y)) if weights is None else weights
    mu = (s[:, None] * X).sum(0) / s.sum(); b = float((s * y).sum() / s.sum()); centred = X - mu
    w = np.linalg.solve(centred.T @ (s[:, None] * centred) + RIDGE * np.eye(X.shape[1]), centred.T @ (s * (y - b)))
    return b, w, mu


class Trees:
    def __init__(self, X, y, keys):
        self.column, self.threshold = [], []
        for j, key in enumerate(keys):
            values = X[:, j]
            cuts = np.unique(np.quantile(values, np.linspace(0.1, 0.9, 9))) if key in NUMERIC else (1.0, 2.0, 3.0)
            for cut in cuts:
                above = int((values >= cut).sum())
                if LEAF <= above <= len(values) - LEAF: self.column.append(j); self.threshold.append(float(cut))
        self.column = np.array(self.column, int); self.threshold = np.array(self.threshold)
        B = self.bits(X); self.base = float(y.mean()); self.trees = []; fitted = np.full(len(y), self.base)
        for _ in range(TREES):
            tree = self.grow(B, y - fitted, np.ones(len(y), bool), 2)
            self.trees.append(tree); fitted += RATE * self.walk(tree, B)

    def bits(self, X): return (X[:, self.column] >= self.threshold).astype(np.float64) if len(self.column) else np.zeros((len(X), 0))

    def grow(self, B, residual, mask, depth):
        count = float(mask.sum()); total = float(residual[mask].sum()); leaf = total / (count + RIDGE)
        if not depth or count < 2 * LEAF or not B.shape[1]: return leaf
        m = mask.astype(np.float64); left_n = B.T @ m; left_g = B.T @ (residual * m)
        gain = left_g ** 2 / (left_n + RIDGE) + (total - left_g) ** 2 / (count - left_n + RIDGE) - total ** 2 / (count + RIDGE)
        gain[(left_n < LEAF) | (count - left_n < LEAF)] = -1.0
        best = int(np.argmax(gain))
        if gain[best] <= 1e-12: return leaf
        side = B[:, best] > 0
        return (best, self.grow(B, residual, mask & side, depth - 1), self.grow(B, residual, mask & ~side, depth - 1))

    def walk(self, tree, B):
        if not isinstance(tree, tuple): return np.full(len(B), tree)
        side = B[:, tree[0]] > 0
        return np.where(side, self.walk(tree[1], B), self.walk(tree[2], B))

    def predict(self, X):
        B = self.bits(X); return self.base + RATE * sum(self.walk(tree, B) for tree in self.trees)


class Fit:
    """Every compared model fitted on one snapshot of a gate's table."""
    def __init__(self, rows, warm):
        features_list = [features for features, _ in rows]; y = np.array([value for _, value in rows], np.float64); n = len(rows)
        self.keys = sorted({key for features in features_list for key in features}); self.index = {key: i for i, key in enumerate(self.keys)}
        X = design(self.index, features_list); self.ymean = float(y.mean())
        self.intercept, self.weights, _, _ = _ridge(rows, RIDGE, PASSES, warm)
        self.w = np.array([self.weights.get(key, 0.0) for key in self.keys])
        self.exact = solve(X, y)
        shapes = [shaped(features) for features in features_list]
        self.shape_index = {key: i for i, key in enumerate(sorted({key for features in shapes for key in features}))}
        self.shape = solve(design(self.shape_index, shapes), y)
        # Hurdle: pass probability by penalised IRLS, then one ridge per side.
        passed = y >= 1.0; self.sides = []
        A = np.hstack([np.ones((n, 1)), X - X.mean(0)]); self.centre = X.mean(0); theta = np.zeros(A.shape[1])
        penalty = RIDGE * np.eye(A.shape[1]); penalty[0, 0] = 0.0
        if 0 < passed.sum() < n:
            for _ in range(8):
                eta = np.clip(A @ theta, -30, 30); p = 1 / (1 + np.exp(-eta)); s = np.maximum(p * (1 - p), 1e-6)
                theta = np.linalg.solve(A.T @ (s[:, None] * A) + penalty, A.T @ (s * (eta + (passed - p) / s)))
        else:
            theta[0] = 30.0 if passed.all() else -30.0
        self.theta = theta
        for side in (passed, ~passed):
            self.sides.append(solve(X[side], y[side]) if side.sum() >= MINIMUM else (float(y[side].mean()) if side.any() else 1.0, None, None))
        # Nearest neighbours on owned items, residuals of the shipped ridge.
        self.items = np.array([j for j, key in enumerate(self.keys) if key.split(':')[0] in ('card', 'up', 'relic')], int)
        self.train_items = X[:, self.items]; self.residual = y - (self.intercept + X @ self.w)
        self.trees = Trees(X, y, self.keys)

    @staticmethod
    def linear(model, X):
        b, w, mu = model
        return np.full(len(X), b) if w is None else b + (X - mu) @ w

    def predict(self, features_list):
        X = design(self.index, features_list); base = self.intercept + X @ self.w
        p = 1 / (1 + np.exp(-np.clip(np.hstack([np.ones((len(X), 1)), X - self.centre]) @ self.theta, -30, 30)))
        near = []
        for row in X[:, self.items]:
            distance = np.abs(self.train_items - row).sum(1); k = min(NEIGHBOURS, len(distance))
            near.append(self.residual[np.argpartition(distance, k - 1)[:k]].sum() / (k + RIDGE))
        return {'mean': np.full(len(X), self.ymean), 'ridge': base, 'ridge*': self.linear(self.exact, X),
                'ridge+shape': self.linear(self.shape, design(self.shape_index, [shaped(features) for features in features_list])),
                'hurdle': p * self.linear(self.sides[0], X) + (1 - p) * self.linear(self.sides[1], X),
                'ridge+knn': base + np.array(near), 'gbdt': self.trees.predict(X)}


nn.Fit = Fit
nn.MODELS = ('prod', 'ridge', 'ridge*', 'ridge+shape', 'hurdle', 'ridge+knn', 'gbdt')

if __name__ == '__main__':
    nn.main()
