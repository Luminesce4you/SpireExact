"""Root rollout policies: a lineage keeps the card-picking family it started with.

A policy is a static table of per-label tiers that the native rollout applies
before its own hand-written score (`policy_prior`). `native` is the empty table.
The other tables come from community statistics (see policy_data.py); on their
own they were no better than the native policy on 23 paired TRAIN seeds, but
each one builds a different deck and gets furthest on different seeds
(experiments/iteration-043/results.md). They are used for that diversity only.

Allocation only: a tier never changes legality, never excludes an option from
the macro search and never bounds anything.
"""
from __future__ import annotations
from .policy_data import TIERS

NATIVE = 'native'
POLICIES = {NATIVE: {}, **TIERS}
# The native host rejects tiers outside this range.
LIMIT = 8


def resolve(names) -> tuple:
    """Validated extra root policies, in request order, without `native`."""
    seen = []
    for name in names:
        if name not in POLICIES:
            raise ValueError('unknown root policy: ' + str(name))
        if name != NATIVE and name not in seen:
            seen.append(name)
    return tuple(seen)


def register(name: str, table: dict) -> None:
    """Root-policy-table hook: adds a static tier table under `name` (plugins
    call it before the root policies are validated). A table maps option
    labels to per-act integer tiers within +-LIMIT. Registering the same
    table again is a no-op; a different table under a taken name is refused."""
    if type(name) is not str or not name or name == NATIVE:
        raise ValueError('a registered policy needs a name other than native')
    if type(table) is not dict or not table:
        raise ValueError('a registered policy needs a nonempty table')
    rows = {}
    for label, row in table.items():
        if (type(label) is not str or type(row) not in (list, tuple) or not row
                or any(type(t) is not int or not -LIMIT <= t <= LIMIT for t in row)):
            raise ValueError(f'policy {name}: tiers of {label!r} must be integers within +-{LIMIT}')
        rows[label] = list(row)
    if name in POLICIES:
        if POLICIES[name] != rows:
            raise ValueError('a different policy table is already registered as ' + name)
        return
    POLICIES[name] = rows


def merge(base: dict, learned: dict) -> dict:
    """Tiers for one request: the lineage's table plus this seed's learned
    tiers, added per act. A table row shorter than the other one repeats its
    last entry, the way the host reads a row for later acts."""
    if not base:
        return learned
    if not learned:
        return {label: list(row) for label, row in base.items()}
    merged = {}
    for label in sorted(set(base) | set(learned)):
        a, b = base.get(label), learned.get(label)
        if a is None or b is None:
            row = list(a if b is None else b)
        else:
            row = [max(-LIMIT, min(LIMIT, a[min(i, len(a) - 1)] + b[min(i, len(b) - 1)])) for i in range(max(len(a), len(b)))]
        if any(row):
            merged[label] = row
    return merged
