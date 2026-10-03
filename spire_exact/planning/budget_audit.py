"""Conservative wall-clock exclusion for the pinned offline Evaluate path.

This does not certify exhaustive search or determinism beyond time gates. With
at most 8 reserved layers, every local slice is >= (B - T) / 8. If 9*T < B,
no local slice or global time gate can have fired during the measured request.
T includes root capture and solve; one millisecond covers integer clock rounding.
"""
from math import isfinite


def clock_gate_audit(search):
    reported = search.get('time_boundary')
    budget, wall = search.get('budget_ms'), search.get('wall_us')
    valid = (type(budget) in (int, float) and type(wall) in (int, float)
             and isfinite(budget) and isfinite(wall) and budget > 0 and wall >= 0)
    sufficient_margin = valid and 9 * (wall / 1000 + 1) < budget
    return {
        'reported_time_boundary': reported,
        'sufficient_margin': bool(sufficient_margin),
        'clock_gates_excluded': reported is False and bool(sufficient_margin),
        'wall_to_budget_ratio': wall / (budget * 1000) if valid else None,
        'scope': 'pinned Evaluate path, at most eight reserved turn layers; sufficient condition only',
    }
