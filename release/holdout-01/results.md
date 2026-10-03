# holdout-01 historical results

This evidence belongs to iteration-055 / frozen-i054a, source version bc57922b87b8b132cf9bd5f3cceffdd58efbec8e615128c67a38caf258a79c2d. It records 20 fresh IRONCLAD A10 full-information seeds, one run per seed, a 30-minute wall cap, and the A + B + open configuration.

**17/20 seeds produced independently replayed native-host wins (85%; Wilson 95% interval 64.0%–94.8%).** All 17 certificates and complete action witnesses are included. Their contexts, trace lengths, canonical SHA-256 digests and historical host fingerprints match the original completed reports. No new game execution was performed while exporting this evidence.

Requested workers: 7. Observed workers: 5 in 3 runs, 6 in 7 runs, 7 in 10 runs. These machine conditions differ; time-to-win values are descriptive. There is no control arm. An unsolved run is not proof that its seed is unwinnable. One observation per seed does not estimate that seed’s success probability.

| Seed | Result | Candidate (solver s) | Verified final report (solver s) | Wrapper s | Job/table s | Evals | Workers | Start MiB |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 2059734609 | verified | 203.8 | 230.9 | 237.9 | 243.9 | 68 | 5 | 11047 |
| 70753827 | verified | 460.8 | 478.4 | 485.8 | 491.4 | 335 | 7 | 15502 |
| 82854315 | verified | 674.1 | 688.3 | 696.3 | 705.6 | 199 | 6 | 12693 |
| 147409771 | verified | 543.2 | 558.9 | 567.0 | 572.7 | 293 | 6 | 12394 |
| 304804409 | unsolved | — | — | 1781.1 | 1787.0 | 476 | 5 | 11952 |
| 1350866973 | verified | 204.9 | 216.2 | 224.1 | 230.5 | 148 | 6 | 10990 |
| 940335177 | verified | 140.6 | 160.5 | 167.6 | 173.6 | 71 | 6 | 13688 |
| 524130501 | unsolved | — | — | 1785.0 | 1792.3 | 1027 | 6 | 13651 |
| 1596179791 | unsolved | — | — | 1800.6 | 1784.9 | 348 | 6 | unrecorded |
| 1150782509 | verified | 657.6 | 670.7 | 685.5 | 692.7 | 362 | 5 | 9769 |
| 1014125957 | verified | 1046.4 | 1061.6 | 1080.9 | 1090.1 | 499 | 7 | 18007 |
| 247940282 | verified | 590.9 | 602.1 | 610.8 | 617.6 | 350 | 7 | 17790 |
| 39174622 | verified | 516.1 | 542.0 | 550.6 | 557.5 | 179 | 7 | 17125 |
| 29598892 | verified | 155.7 | 169.5 | 178.3 | 185.6 | 120 | 7 | 15243 |
| 1433807030 | verified | 816.7 | 841.8 | 853.8 | 862.7 | 441 | 6 | 14170 |
| 1899537279 | verified | 197.9 | 210.2 | 220.2 | 235.4 | 81 | 7 | 13214 |
| 584844114 | verified | 1396.7 | 1417.8 | 1428.0 | 1444.3 | 336 | 7 | 14718 |
| 1375284397 | verified | 189.9 | 204.5 | 212.7 | 219.5 | 159 | 7 | 18720 |
| 361226862 | verified | 390.7 | 412.1 | 421.1 | 428.1 | 242 | 7 | 18649 |
| 2098492050 | verified | 75.3 | 92.0 | 100.0 | 106.9 | 60 | 7 | 18295 |

Candidate times use the solver/coordinator time origin. “Verified final report” is the final recorded elapsed_seconds after verification and cleanup; the exact first-verification event timestamp is unavailable. Wrapper and queue times have different origins and overhead. The job/table time for seed 1596179791 comes from its final solver report because its queue log row was not captured; its solver result and wrapper/resource files are present.

First-candidate median: 460.8 s; maximum: 1396.7 s. Historical candidate-based empirical solved share: 300 s 35%, 600 s 60%, 900 s 75%, 1200 s 80%, 1800 s 85%. This is not a per-seed reliability estimate.

The three unsolved seeds are 304804409, 524130501 and 1596179791. Their UNKNOWN/time-budget results remain in summary.json, including separate resource-limit and timeout counts; no failed seed was removed.

Certificate proof scope: installed game commands in offline TestMode. Normal Godot game equivalence was not verified. The action witnesses are compact replay inputs with context and complete trace; game binaries, full serialized saves and per-action observations are excluded.

The certificates bind the historical native host and game fingerprints. A source package rebuilt today produces a new binary identity and must be verified independently; these historical certificates and the 85% study result are not evidence for the newly compiled binary.

panel.json and manifests are sanitized extracts. summary.json and this report are new extracted summaries. Certificate and winning-route files preserve the original bytes. provenance.json records original file hashes using portable references; it does not expose private local paths or coordination text.

Replaying a witness still requires separately supplied compatible game dependencies and the documented native replay command; this export did not perform replay.
