# Pinned offline count-budget audit — 2026-10-01

Scope: upstream commit `4d2c55069f2cf9f8c621631594857b412cf9660f`,
CombatSolver DLL `0af01356be8b46bce4d2d17088e83184d7205c8958c089c8d9dcaec312e38c32`.
This is a source audit, supplemented by request telemetry. It is not a universal
proof of deterministic or exhaustive combat search.

## Actual production path

`BeamAdvisor` calls `OfflineSearchHarness.ModRuntime.RunSearchDetailed` in Evaluate
mode. `SolveEvaluate` (ModRuntime.cs:327–372) constructs one CombatBeamSolver,
passes no progress callback and publishes total expanded nodes. DOP is 1.
`ApplyFixedBudgetSettings` disables StopAtAcceptableBattleHpLoss and uploads;
the unattended override sets FixedSearchBudget. **FixedBudget does not disable all
clock checks.** Beam/filter semantics remain upstream heuristic proposals.

## Clock-sensitive branches still present

- `CombatBeamSolver.Phases.cs:1374,1443–1535`: reserves 4 normal / 8 boss turn
  layers (SolverWeights.cs:86–87). A layer may end by elapsed time or expanded
  nodes. Act3BossStrategy disables the local time slice only for its recognized
  boss encounters; this does not disable the global safety limit.
- `Phases.cs:1586–1608`: global elapsed-time safety ends active play expansion.
- `Phases.cs:1903–1908`: a fetched-power follow-up checks elapsed time before
  further expansion, without a dedicated report at that exact branch.
- `CombatBeamSolver.NoveltySearch.cs:96`: novelty search has a time exit; current
  Evaluate request does not request novelty. Coordinator/portfolio paths have
  further remaining-time policies and are outside this path's evidence.
- `SearchWorkPacer.cs:35–39`: periodic Thread.Yield changes scheduling; it does
  not itself choose an action. Headless CaptureSearchPolicy disables frame
  recovery waits (SolverController.cs:494–498).
- UI route preview is gated by a non-null progress callback (Phases.cs:911),
  absent here. Its wall-clock frequency therefore does not select this path.

## Reporting limit and conservative check

ModRuntime records `TURN_LAYER_BUDGET reason=time`, or final BoundaryReason equal
to TimeLimit. In Phases.cs:511, TimeLimit replaces a candidate boundary only if
that boundary was None. Thus a false `time_boundary` flag alone is insufficient
to rule out every clock-sensitive branch. Existing duplicate trace/node tests
prove their tested samples; their old `count_boundary_only` label is not a
general guarantee for arbitrary longer searches.

Use a sufficient margin test on the already-exported per-search wall time T and
budget B. With R <= 8 reserved layers, a local slice is at least (B - T)/8.
If `9 * (T_ms + 1) < B_ms`, both local and global clock gates are excluded for
that completed request. The extra millisecond covers integer-clock rounding.
T comes from the outer stopwatch enclosing root capture and solve. This is
conservative: failing the test means **unproven**, not that a clock actually fired.
Missing telemetry never passes. A reported boundary always fails. The helper is
`spire_exact/planning/budget_audit.py`; update its bound if the pinned solver or
execution path changes.

## Other limits

No-GC lifecycle / memory-pressure recovery is opt-in and currently disabled by
HarnessOptions. Ordinary GC still affects elapsed time. Turning No-GC on would
need a separate audit of runtime memory-dependent admissions and stopping.
The native task/queue timeout, process memory admission, campaign wall cap and
OS Job limit remain safety bounds. A truncated job is a censored result, not a
reproducible count-complete run or a proof of infeasibility. Ordered macro result
absorption and DOP 1 do not remove these external limits.

The running frozen-i035 baseline and queued frozen-i036 profiler are unchanged.
Apply this supplementary telemetry audit to their results; no current source
inspection justifies retroactively claiming or rejecting any seed victory.
