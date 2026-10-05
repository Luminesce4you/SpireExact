# i085 macro research log

Baseline: `4c5823acd1a68acb797069fb8f7522a6ad857bbf` (`codex/i082-release`).
Research branch: `codex/i085-macro-candidate-20261005`.

## Evidence at the initial checkpoint

This file is a research checkpoint, not a completed solver release. No i085
native builds, rollouts, wins, or performance comparisons have been run.
The research container was checked: Python 3.13.5 and Git are available;
.NET is absent and direct GitHub cloning fails DNS resolution. The connected
GitHub API can read and modify the isolated research branch. The read-only
CI workflow must be inspected before any test success can be claimed.

## Findings checked against source control flow

1. `FocusScheduler.add` rejects a trajectory when its utility tuple is already
   represented, before considering its actual decisions or deck. Equal terminal
   outcomes do not imply equal macro neighborhoods. Hypothesis: this suppresses
   useful plateau diversity. No win-rate loss is established yet.
2. `IndexedStrategicScheduler.add` samples a fixed evenly spaced subset of wide
   menus. Remaining alternatives are counted as deferred but not stored in a
   recovery queue. Focus retains uncapped alternatives only for admitted pool
   sources. General claims of eventual explorer coverage are therefore too
   strong for rejected/evicted sources.
3. `PreparationCandidates.observe` proposes routes after gold/HP triggers;
   `NativeMap.path` minimizes elite count before hops. This is a narrow proposal
   family, not a full-information strategic route comparison.
4. `solve` drains dispatchable urgent proposals before repair/scheduler work.
   Focus:explore = 3:1 applies to macro selections, not to all native evaluations
   or native seconds. A new route portfolio must not flood this queue.
5. Map-route consumers use isolated native runs, exact source-contract checks,
   no checkpoint restoration, and no result-cache reuse. Startup and prefix
   replay costs must be measured rather than assumed negligible.
6. Ordered dispatch can hold 56 ordinary requests for 7 workers. Priors are
   sampled at submission and ordinary results absorbed in order. The practical
   effect of this delay is unknown; smaller windows belong in an ablation.

## Initial decisions

Keep tactical budgets, F1/F2 models, synthetic-evidence boundaries and
independent root replay verification unchanged. Test macro-only changes first.
Do not add a complex bandit without measured feedback. Do not reinterpret
synthetic F2 failure as infeasibility. Avoid card-name strength rules.

## Claims rejected or deferred

- Historical i082 Python tests are not E2E evidence for i085.
- No dominant failure Act, native compute percentage, speedup or win rate has
  been established from the source/documentation inspected so far.
- Route descriptors are allocation hints, not exact state/cache/proof identity.
- One-step shadow disagreement cannot establish an alternate run's outcome.

Final implementation details and actual evidence belong in `docs/I085.md`
and `release/i085/results.md`. This log preserves the initial hypotheses.
