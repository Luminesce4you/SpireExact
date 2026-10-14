"""Group-sequential decision for paired DEV screens (PLAN-v2 R2a point 4).

One block = one seed run in both arms (the control may be a recorded control
or exempt, see control_library.py / exempt_check.py). The block outcome is the
log time ratio d = log(control / treatment) of the capped first-win times, as
in iteration-105, so d > 0 means the treatment is faster. A seed that is
exempt (exempt_check.py) has d = 0 exactly: the treatment would be the same run.

`design` pre-registers the plan: maximum blocks N, looks (block counts),
one-sided alpha, the design effect and per-block SD (both pre-registered, for
example from the A vs A2 spread of iteration-105). Boundaries use Lan-DeMets
alpha spending of O'Brien-Fleming type for efficacy,
    alpha(t) = 2 - 2 Phi(z_{1-alpha/2} / sqrt t)   (one-sided alpha),
and, optionally, beta spending of the same type for a non-binding futility
bound (stop: no benefit of the design size; harm is below it as well). The
efficacy bounds ignore the futility bound (non-binding), so stopping for
futility never inflates alpha. Crossing probabilities are computed exactly by
recursive numerical integration on the Brownian-motion scale.

`monitor` reads the blocks observed so far, recomputes the bounds at the
observed information fractions n_k / N (the spending function makes the
boundaries independent of when the looks actually happen), and reports the
decision at the latest look. The statistic is the studentized mean of d,
converted to the normal scale through the t distribution with n - 1 degrees
of freedom.

  python tools/sequential_test.py design --max-blocks 40 --looks 10,20,30,40 --alpha 0.025 --effect 0.26 --sd 0.6 --out plan.json
  python tools/sequential_test.py monitor --plan plan.json --blocks blocks.jsonl

blocks.jsonl: one JSON object per block with `seed` and either `d` or
`control_seconds` and `treatment_seconds` (already capped), optional `exempt`.
Pure computation; no game process.
"""
from pathlib import Path
import argparse
import datetime
import hashlib
import json
import math

import numpy as np
from scipy import stats

SCHEMA = 'spire-sequential-plan/v1'
GRID = 4001          # points per look for the continuation density
SPAN = 9.0           # +- SPAN standard deviations of the Brownian motion


def now():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat()


def obf_spend(t, level):
    """Lan-DeMets O'Brien-Fleming-type spending of a one-sided `level`."""
    if t <= 0:
        return 0.0
    return float(2 - 2 * stats.norm.cdf(stats.norm.ppf(1 - level / 2) / math.sqrt(min(t, 1.0))))


class Recursion:
    """Sub-density of S(t_k) = Z_k sqrt(t_k) on the continuation region,
    for a Brownian motion with drift `theta` (per unit information)."""

    def __init__(self, theta):
        self.theta, self.t, self.x, self.f = theta, 0.0, None, None

    def tail(self, t, c, upper):
        """P(continued so far and S(t) > c) (upper) or < c (lower)."""
        dt = t - self.t
        mean, sd = self.theta * dt, math.sqrt(dt)
        if self.x is None:
            z = (c - mean) / sd
            return float(stats.norm.sf(z) if upper else stats.norm.cdf(z))
        z = (c - self.x - mean) / sd
        g = stats.norm.sf(z) if upper else stats.norm.cdf(z)
        return float(np.trapz(self.f * g, self.x))

    def advance(self, t, low, high):
        """Moves to look t keeping only paths with low < S(t) < high."""
        dt = t - self.t
        mean, sd = self.theta * dt, math.sqrt(dt)
        width = SPAN * math.sqrt(t)
        lo, hi = max(low, self.theta * t - width), min(high, self.theta * t + width)
        x = np.linspace(lo, hi, GRID) if hi > lo else np.array([lo, lo])
        if self.x is None:
            f = stats.norm.pdf((x - mean) / sd) / sd
        else:
            kernel = stats.norm.pdf((x[:, None] - self.x[None, :] - mean) / sd) / sd
            f = np.trapz(kernel * self.f[None, :], self.x, axis=1)
        self.t, self.x, self.f = t, x, f


def solve(fn, target, lo=-10.0, hi=12.0):
    """Root of the monotone decreasing fn(c) - target by bisection."""
    for _ in range(80):
        mid = (lo + hi) / 2
        if fn(mid) > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def boundaries(fractions, alpha, beta=None, theta=None):
    """Efficacy (and futility) bounds on the Z scale at the information
    fractions; with futility, also the stopping probabilities under H1."""
    h0 = Recursion(0.0)
    upper, spent = [], 0.0
    for t in fractions:
        increment = obf_spend(t, alpha) - spent
        c = solve(lambda c: h0.tail(t, c * math.sqrt(t), True), increment)
        upper.append(c)
        spent += increment
        h0.advance(t, -math.inf, c * math.sqrt(t))
    out = {'efficacy_z': upper, 'alpha_spent': [obf_spend(t, alpha) for t in fractions]}
    if beta is None or theta is None:
        return out
    h1 = Recursion(theta)
    lower, power, futile, spent = [], [], [], 0.0
    for k, t in enumerate(fractions):
        c = upper[k] * math.sqrt(t)
        increment = obf_spend(t, beta) - spent
        a = solve(lambda a: -h1.tail(t, a * math.sqrt(t), False), -increment)
        a = min(a, upper[k])
        if k == len(fractions) - 1:
            a = upper[k]   # the last look decides
        lower.append(a)
        power.append(h1.tail(t, c, True))
        futile.append(h1.tail(t, a * math.sqrt(t), False))
        spent += futile[-1]
        h1.advance(t, a * math.sqrt(t), c)
    out.update(futility_z=lower, power_by_look=power, futility_by_look_h1=futile, power=sum(power))
    return out


def design(args):
    looks = [int(v) for v in args.looks.split(',')]
    if looks != sorted(set(looks)) or looks[-1] != args.max_blocks:
        raise SystemExit('--looks must increase and end at --max-blocks')
    fractions = [n / args.max_blocks for n in looks]
    theta = args.effect / args.sd * math.sqrt(args.max_blocks) if args.effect else None
    bounds = boundaries(fractions, args.alpha, args.beta if args.futility else None, theta)
    plan = {'schema': SCHEMA, 'created': now(), 'max_blocks': args.max_blocks, 'looks': looks, 'alpha_one_sided': args.alpha,
            'beta': args.beta if args.futility else None, 'futility': args.futility,
            'design_effect_log_ratio': args.effect, 'design_sd': args.sd, 'drift_at_full_information': theta,
            'outcome': 'd = log(control / treatment) of capped first-win seconds; d > 0: treatment faster; exempt seed: d = 0',
            'spending': 'Lan-DeMets O\'Brien-Fleming type', 'grid': GRID, **bounds,
            'preregistration_note': args.note}
    if theta is not None and not args.futility:
        h1 = Recursion(theta)
        power = 0.0
        for t, c in zip(fractions, bounds['efficacy_z']):
            power += h1.tail(t, c * math.sqrt(t), True)
            h1.advance(t, -math.inf, c * math.sqrt(t))
        plan['power'] = power
    text = json.dumps(plan, ensure_ascii=False, indent=2)
    plan['plan_sha256'] = hashlib.sha256(text.encode()).hexdigest()
    Path(args.out).write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: plan.get(k) for k in ('looks', 'efficacy_z', 'futility_z', 'power')}, ensure_ascii=False))


def block_d(row):
    if row.get('exempt'):
        return 0.0
    if 'd' in row:
        return float(row['d'])
    return math.log(float(row['control_seconds']) / float(row['treatment_seconds']))


def monitor(args):
    plan = json.loads(Path(args.plan).read_text(encoding='utf-8'))
    blocks = [json.loads(line) for line in Path(args.blocks).read_text(encoding='utf-8').splitlines() if line.strip()]
    seeds = [b['seed'] for b in blocks]
    if len(set(seeds)) != len(seeds):
        raise SystemExit('a seed appears twice')
    n, N = len(blocks), plan['max_blocks']
    if n > N:
        raise SystemExit(f'{n} blocks exceed the planned maximum {N}')
    looks = [k for k in plan['looks'] if k <= n]
    if args.look_now and (not looks or looks[-1] != n):
        looks.append(n)
    if not looks:
        print(json.dumps({'blocks': n, 'decision': 'continue', 'reason': 'before the first planned look'}, ensure_ascii=False))
        return
    fractions = [k / N for k in looks]
    bounds = boundaries(fractions, plan['alpha_one_sided'], plan.get('beta') if plan.get('futility') else None,
                        plan.get('drift_at_full_information'))
    d = np.array([block_d(b) for b in blocks])
    history, decision = [], 'continue'
    for i, k in enumerate(looks):
        x = d[:k]
        sd = float(x.std(ddof=1)) if k > 1 else float('nan')
        t_stat = float(x.mean() / (sd / math.sqrt(k))) if sd > 0 else 0.0
        z = float(stats.norm.ppf(stats.t.cdf(t_stat, k - 1))) if k > 1 else 0.0
        up = bounds['efficacy_z'][i]
        low = bounds.get('futility_z', [None] * len(looks))[i]
        state = 'efficacy' if z >= up else ('futility' if low is not None and z <= low else 'continue')
        if k == N and state == 'continue':
            state = 'futility'
        history.append({'blocks': k, 'mean_d': float(x.mean()), 'ratio': math.exp(float(x.mean())), 'sd': sd, 'z': z,
                        'efficacy_z': up, 'futility_z': low, 'state': state,
                        'wilcoxon_p_one_sided': float(stats.wilcoxon(x, alternative='greater', zero_method='zsplit').pvalue)
                        if k > 1 and np.any(x != 0) else None})
        if state != 'continue':
            decision = state
            break
    report = {'plan': args.plan, 'plan_sha256': plan.get('plan_sha256'), 'blocks': n,
              'exempt_blocks': sum(bool(b.get('exempt')) for b in blocks), 'looks': history, 'decision': decision,
              'note': 'efficacy: treatment faster beyond the bound; futility: no benefit of the design size '
                      '(non-binding, it never inflates alpha); decided at the first look that crossed'}
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=1))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('design')
    p.add_argument('--max-blocks', type=int, required=True)
    p.add_argument('--looks', required=True, help='comma-separated block counts, ending at --max-blocks')
    p.add_argument('--alpha', type=float, default=0.025, help='one-sided')
    p.add_argument('--futility', action='store_true')
    p.add_argument('--beta', type=float, default=0.2)
    p.add_argument('--effect', type=float, help='design effect, log(control / treatment)')
    p.add_argument('--sd', type=float, help='pre-registered SD of d per block')
    p.add_argument('--note', default='')
    p.add_argument('--out', required=True)
    m = sub.add_parser('monitor')
    m.add_argument('--plan', required=True)
    m.add_argument('--blocks', required=True)
    m.add_argument('--look-now', action='store_true', help='also look at the current block count (unplanned look)')
    m.add_argument('--out')
    args = parser.parse_args()
    if args.command == 'design':
        if args.futility and (args.effect is None or args.sd is None):
            parser.error('--futility needs --effect and --sd')
        design(args)
    else:
        monitor(args)


if __name__ == '__main__':
    main()
