# i085-final01 results — synthetic-campaign evaluation

**No native build, STS2 rollout, new native win, game win rate or measured native speedup is claimed.** Every number below comes from synthetic campaign worlds driving the real planner loop (`tools/sim_campaign.py`); simulated seconds are a cost model of a 7-worker pool.

Base: `a331a2e` (i085 delivery) on top of `4c5823a` (i082). Public default remains i082; final01 needs `--feature-profile i085-final01`. Design and reasoning: [docs/I085_FINAL01.md](../../docs/I085_FINAL01.md).

## Evidence status

| Status | Content |
|---|---|
| implemented | gate clinic (root vs depth per (stem, gate)), clinic focus allocation (re-root / local shares, re-root target act, repair-direction tiers), verdict-aware gate-retry share and order, auxiliary-work governor, version-robust structural prior with per-seed posterior and learned label effects, i085-final01 entry profile and launcher/benchmark arms, F2-readiness read-view cache (all profiles, behaviour-identical), synthetic campaign simulator and paired study tool |
| default | public i082 unchanged; i085-final01 is an explicit research profile |
| unit-tested | 22 final01 tests; full suite 972 run, 969 passed, 0 failures, 2 pre-existing errors, 2 Windows skips ([tests.json](evidence/tests.json), [log](evidence/unittest.log)) |
| compatibility | legacy / i075-final / i081 / i082 resolve unchanged (additive disabled keys only); i082 launcher argv equal; 18 fixture paths / 1,440 requests identical; native/proof/lock files byte-identical ([compatibility.json](evidence/compatibility.json)) |
| development panels | dev (mixed, worlds 2000+) and devhard (long tail, 3000+), used for every design decision ([evidence/README.md](evidence/README.md)) |
| held-out panels | eval (mixed, 1000+) and evalhard (long tail, 4000+), 2 solver seeds, run once on the frozen source ([freeze.json](evidence/freeze.json)) — **running; results are added in the next commit** |
| native-tested | **No** |
| unverified | native F1→F2 tail reduction, verified-win rate, native worker cost and memory of every switch, real-game reliability of the clinic's evidence |

## Development panels (final source, dev-pilot5, 1 solver seed, 45 simulated minutes)

| Arm | solved | RMST (s) | easy | tactical | resource | root_act2 | root_act1 | mixed |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| i054a | 38/40 | 671.4 | 419 | 184 | 781 | 774 | 1217 | 721 |
| i082 | 38/40 | 694.1 | 407 | 176 | 841 | 779 | 1360 | 887 |
| final01 | 38/40 | 601.0 | 384 | 160 | 697 | 591 | 1008 | 687 |
| final01-i081 | 38/40 | 613.9 | 348 | 160 | 753 | 534 | 1236 | 862 |

Paired against i082: final01 −93.1 s (95% world bootstrap [−151.7, −29.6]), 30 faster / 4 slower pairs. The two unsolvable control worlds stay unsolved for every arm. Per-class columns are restricted mean seconds.
