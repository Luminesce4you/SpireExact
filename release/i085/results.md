# i085 results — persistent tail research candidate

**No native build, STS2 rollout, new native win, game win rate or measured native speedup is claimed.**

Algorithm baseline: `4c5823acd1a68acb797069fb8f7522a6ad857bbf` (`codex/i082-release`).
Publication parent: `dec944a41743b83e44f137a140770e16961aa08f`.
Target branch: `codex/i085-macro-candidate-20261005`.
Executable source hash: **`238e835b78f82c3bdefad2c3b7e477bdcbe485e6d170f0e23bcc39c389338820`**.
Exact commit/push status belongs to the external `publication.json`, not an assumed remote update.

## Evidence status

| Status | Actually implemented or executed |
|---|---|
| implemented | Earlier macro plateau retention, real wide-menu recovery, auxiliary admission fairness and bounded native-map route proposals; new persistent exact-prefix sites, hierarchical virtual service with admission floors, pending cost reservations/settlements/requeue accounting, cohort concurrency cap, post-F1 dispatch feedback window, true gate/joint fit clocks, explicit long-budget launcher, censored/per-seed time-scaling audit, guarded Git bundle publisher |
| default | Public i082 unchanged. i085 retains earlier macro switches; new route proposals and new tail allocation are **shadow**, not native-on by default |
| experimental | `i085-tail` actually controls the new macro channel/window; independent wide-window, no-cap and 8/4 fitting-threshold arms |
| unit-tested | **108/108** i085-specific tests passed, source unchanged before/after. [JSON](evidence/tail/python-candidate/tests.json) / [log](evidence/tail/python-candidate/unittest.log) |
| full regression | **950 testsRun, 947 successful methods, 0 assertion failures, 2 errors, 2 skips; NOT green.** Class setup errors are separately counted by unittest. [JSON](evidence/tail/python-full/tests.json) / [log](evidence/tail/python-full/unittest.log) |
| baseline recheck | Unmodified baseline rerun this turn: **842 testsRun, 839 successful methods, 0 failures, same 2 errors, 2 skips**. [JSON](evidence/tail/baseline-full.json) / [log](evidence/tail/baseline-full.log) |
| compatibility | Four old profiles' original resolved fields unchanged; **18 fixture paths / 1,440 request pairs identical**; native/proof/lock files checked byte-identical. Finite no-DLL tests, not universal native execution equivalence. [record](evidence/tail/compatibility.json) |
| static validation | Python compileall/imports, actual release-parser dry120 for shadow/on, five-arm plan and missing-output report. No native command launched. [receipt](evidence/tail/static-validation.json) / [plan-only](evidence/tail/plan-only/plan.json) |
| constructed experiments | **240 final-source trials**, 120 at fixed evaluation cap and 120 at fixed simulated-time cap. Exact goal combinations, both single/parallel windows, raw events/costs/Python timings retained. [evaluation-cap](evidence/tail/constructed-evaluation-cap.json) / [time-cap](evidence/tail/constructed-time-cap.json) |
| rejected prototype | Luby-sized downstream-edit lease removed after mixed/negative constructed results. Its 160 trials and exact five-file overlay remain separately labeled. [rejected evidence](evidence/tail/rejected/README.md) |
| publication tooling | Four tests use an actual **local file-backed bare Git remote** to test safe fast-forward/readback, dry-run, changed remote and wrong bundle rejection. This is NOT a GitHub push or a native test |
| native-tested | **No** |
| benchmarked on STS2 | **No**. Plan/report tests use synthetic saved artifacts and mocks, not the game |
| unverified | Actual F1→F2 tail reduction, first independent verified win, game win rate, native throughput/memory impact, cross-character native use, effective probe cost and model ranking benefit |
| blocked by environment | Linux/source-only, no dotnet, game DLL or installed vendor. Current connected GitHub API exposes read/search/download, not write. Actual CLI push return and remote readback must be checked in publication.json |

## Full-suite errors were not hidden

1. `test_dashboard_controls.DashboardControlTests.test_180_minutes_remains_valid_and_entry_records_latest_options` expects 180 minutes, while public dashboard/ordinary launcher permits 1–45. Explicit research budget does not silently rewrite the public contract.
2. `setUpClass (test_solver_patches.SolverPatchContractTests)` needs vendor/CombatSolver source not present here.
3. Two Windows sharing tests skip on Linux.

Both errors were freshly reproduced on the unmodified source; no baseline test was deleted or marked passing. The prior successful-method count and testsRun do not add in the naive way because class-initialization errors are recorded without a test method run.

## What the mathematical experiments actually show

The virtual-arrival counterexample preserves an old pending flow while repeatedly introducing and retiring a zero-debt newcomer. In 60 rounds the zero-debt rule serves the old flow zero times; the new rule serves it 60 times. This tests a mechanism, not a game distribution or universal starvation theorem.

For independent restart illustration, distribution `(1,.1),(100,.9)` has mean 90.1. Cutoff1 gives expected time10 with no overhead, but190 with20 overhead per failed restart. Repeating an identical deterministic100-unit task at cutoff10 never completes. The native solver has not been shown to satisfy the IID premise. No forced root restart was installed.

Final constructed time panel: **30 simulated units**, window14/7 abstract workers, four complete goal alternatives each, failure capped at30. Values are restricted mean simulated time, followed by toy goals found; not native seconds or game wins:

| Constructed world | i082 focus core | Earlier i085 macro core | New tail on |
|---|---:|---:|---:|
| Early complementary pair | 19.125 (4/4) | 27.150 (1/4) | 17.200 (4/4) |
| Late single change | 3.600 (4/4) | 3.600 (4/4) | 4.200 (4/4) |
| Late complementary pair | 17.325 (3/4) | 7.775 (4/4) | 9.525 (4/4) |
| Expensive late distraction | 28.700 (1/4) | 30.000 (0/4) | 29.075 (1/4) |
| Budget-unsolved control | 30.000 (0/4) | 30.000 (0/4) | 30.000 (0/4) |

Raw files also include all window1 cases, evaluation-cap cases and negative outcomes. The last two single/parallel configurations are not claimed as actual worker-scaling measurements. Different evaluation/time caps give different rankings. In-flight toy successes not observed before cap do not count.

**The mixed evidence is why tail control is shadow by default.** Do not extract only the early-pair row and claim an overall speedup. The older macro core can itself regress versus i082; keep i082 as an actual comparator rather than treating the previous candidate as established production.

## F2 threshold discovery

Ordinary real-gate `add` first fit checks both minimum and dirty refresh, whereas joint first fit checks minimum and only later fits check refresh. With a no-rescaling fixture, minimum8/refresh16 leaves the real gate unfitted at8 (fits at16), but joint fits at8; minimum8/refresh4 lets real gate fit at8. Explicit real `refit()` is a separate path; a forced final-save refit is not evidence of earlier dispatch benefit.

Default remains24/16; `i082-low-threshold` isolates8/4. The 5/10 readiness completeness condition, cadence20, shared synthetic samples, borrowed F1 labels and delayed dispatch remain distinct issues. User-reported >30min F1→F2 intervals motivate diagnostics but cannot identify the causal bottleneck alone.

## Provenance and recovery boundary

The earlier full macro snapshot was recovered. The later continuation/kernel source was not preserved in the available complete source archive or remote branch; only documentation/test summaries remained. They are archived under [previous-continuation-summary](evidence/tail/previous-continuation-summary/README.md), never relabeled as this candidate's test results. The current implementation was rebuilt and tested against its own hash, and a complete source archive/patch/bundle is delivered.

Intermediate receipts and rejected experiments have their original source hashes. Current primary receipts live directly under `evidence/tail/python-*`, `compatibility.json`, and `constructed-*.json`; archived macro evidence is under `evidence/previous-macro`. [Provenance](source-provenance.json) lists primary evidence digests. Original i082/holdout evidence and SOURCE_MANIFEST were not rewritten.

The recovered historical P5 multi-seed upload described A0/180sec/48evaluation runs, not current A10 outliers, so it was not used to fit or benchmark i085. [exclusion record](evidence/tail/excluded-historical-data.json)

## Native validation and acceptance

See [I085_TAIL.md](../../docs/I085_TAIL.md) for equations, limits, rejected ideas, current switches, Windows smoke/main-panel commands and distribution metrics. `tail-panel.json` is public development, not holdout. The report groups repeated solver seeds within each game seed; four repetitions do not support a reliable p95 or fitted power law.

Run `i082,i082-low-threshold,i085-core,i085,i085-tail` at a matched cap and fixed source/native/resources. Inspect missingness, resource failures and rejected verification, not just survivor-only success. True fresh30/60/120 experiments must be new blocks; a long-run prefix is not relabeled as a fresh short-budget trial. Final evidence must include independent root replay, not F2 pass or synthetic viability.
