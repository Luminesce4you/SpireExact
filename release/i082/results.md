# i082 recorded results and release evidence

Frozen source: e1b10f6f10f1c1d27f5f176f8b4e73ce8aa07fc2a9ab94b9eaf2d9cd10bd7c74. Snapshot exported 2026-10-04T22:41:08+08:00.

- Existing regression: **845/845 passed**, failures/errors/skipped 0; recorded Python **3.10.6**, Windows. Native runtime tests in that suite: **0**. This is the original test environment, not a fresh package test.
- Profile/default integration is implemented in the frozen branch. i081, i075-final and legacy retain their recorded resolution rules; paired-card-joint follows its three prerequisites.
- No completed i082 whole-run win or measured benefit is reported at this snapshot. [The existing 60-minute job](existing-run-progress.json) is still UNKNOWN, reached floor 48, and has no certificate. Its 896 evaluations and 1134.4 solver seconds are progress counters.
- The current local SpireBoard entry has been independently verified as **i082**. Its frozen source, compiled host, deployment report and complete profile defaults agree; the active service reads that profile for new launches. [Local deployment evidence](deployment.json) records a zero-action initialization and read-only API checks. The portable source bundle has its own [frontend readiness report](../FRONTEND_VALIDATION.json).
- The historical holdout-01 **17/20** belongs to frozen-i054a / source bc57922b87b8b132cf9bd5f3cceffdd58efbec8e615128c67a38caf258a79c2d and its A+B+open configuration. Those historical certificates and results do not become i082 evidence.

Historical cost context quoted by the i082 plan: the earlier i080 run produced three paired tables using about 325 cumulative native seconds, about 2% of worker time. The estimated i082 cost increase is unmeasured and is not a release performance claim.

The default entry is defined in [defaults.json](defaults.json). Scope, provenance, original-file hashes and the live-run timestamp are separate so that unit tests, existing progress and historical wins retain their own attribution. Source-build reports are added by the package builder.

Original results reference: P5plus-f1-card-switches/experiments/iteration-082/results.md.
Original results SHA-256: b1be1fcc449a2aa5a1bfe04335ec2570f61db21ec1541e0dd80f46011a1bb49d.
This Markdown is a sanitized summary, not the original byte stream.
