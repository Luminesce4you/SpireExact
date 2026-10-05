# i100 results

i100 (research name `final01`) is the public default from this release on. The evaluation below drives the real planner loop (`search.solve`: dispatch, absorption, gate clinic, gate retries, independent replay verification) against seeded synthetic three-act campaigns with a final double boss; the worker pool runs on a simulated clock, so seconds are the simulator's cost model of a 7-worker pool. Design: [docs/I100.md](../../docs/I100.md).

Base: `a331a2e` (i085 delivery) on `4c5823a` (i082). Release source identity: [defaults.json](defaults.json) `executable_source_hash`.

## Evidence

| Item | Result |
|---|---|
| Python suite | 980 run, 978 passed, 0 failures, 1 error, 2 skips. The error is `test_solver_patches` setup, which needs `vendor/CombatSolver` fetched by `tools/setup_source.py`; the skips are Windows-only ([tests.json](evidence/tests.json), [log](evidence/unittest.log)) |
| Frontend checks | 5 / 5 dashboard JS checks pass ([js-checks.log](evidence/js-checks.log)) |
| Compatibility | legacy / i075-final / i081 / i082 unchanged when selected; i082 launcher argv equal; 18 fixture paths / 1,440 requests identical; native differences only the two files of the combat-boundary fix ([compatibility.json](evidence/compatibility.json)) |
| Held-out panels | 80 worlds × 2 solver seeds × 9 configurations, run once after the code freeze ([freeze.json](evidence/freeze.json), raw rows in [sim/](evidence/sim/)) |
| Release code recheck | 12 held-out runs repeated with the release code: all identical ([recheck](evidence/sim/recheck-final-code.log)) |
| Combat-boundary fix | source contract tests (`tests/test_combat_boundary.py`) fail on the previous native source and pass now; clinic and readiness regression tests cover fights that open with card selections |

## Both held-out panels pooled (160 runs per configuration)

Each panel contains 2 unsolvable control worlds, so 156 / 160 is every solvable run.

| Configuration | solved 30 min | solved 45 min | RMST 30 (s) | RMST 45 (s) | paired vs i100, 45 min (95% world bootstrap) |
|---|---:|---:|---:|---:|---|
| **i100** | 154 / 160 | 156 / 160 | 579.1 | 607.2 | — |
| i054a (holdout-01 configuration) | 156 / 160 | 156 / 160 | 636.3 | 658.8 | +51.6 s [-2.9, +103.5] · 67 faster / 85 slower |
| i082 | 145 / 160 | 156 / 160 | 704.0 | 745.8 | +138.6 s [+88.7, +195.2] · 25 faster / 110 slower |
| i085 | 142 / 160 | 155 / 160 | 702.2 | 755.0 | +147.8 s [+90.4, +208.4] · 25 faster / 108 slower |
| i085 tail on | 151 / 160 | 156 / 160 | 612.9 | 641.5 | +34.2 s [-32.2, +102.9] · 65 faster / 75 slower |
| i100 − clinic | 154 / 160 | 156 / 160 | 641.8 | 667.9 | +60.6 s [+10.6, +113.9] · 36 faster / 57 slower |
| i100 − repair hints | 155 / 160 | 156 / 160 | 582.4 | 610.3 | +3.1 s [-19.2, +25.0] · 28 faster / 27 slower |
| i100 − prior and hints | 155 / 160 | 155 / 160 | 588.6 | 616.7 | +9.5 s [-16.9, +37.1] · 37 faster / 40 slower |
| i100 + i081/i082 synthetic F2 | 152 / 160 | 154 / 160 | 590.4 | 624.7 | +17.5 s [-23.3, +59.3] · 37 faster / 65 slower |

- i100 against i082: 139 s faster on average, 110 faster / 25 slower pairs; 154 vs 145 solved by 30 minutes.
- i100 against i054a: 52 s faster on average; the long-tail panel alone is a tie (−5 s), the mixed panel favours i100 (−98 s).
- The gate clinic carries the gain: removing it costs 61 s [+11, +114]. Repair hints and the structural prior change little on the held-out panels.

## Default configuration

Rule, declared before the long-tail panel was read: Declared before evalhard was read: pool both held-out panels; enable the i081/i082 synthetic F2 machinery by default only if final01-i081 has the lower pooled mean restricted time and is not slower on the easy class.

Result: pooled difference of the i081/i082 synthetic F2 machinery +17.5 s, 95% interval [-23.3, 59.3]; easy class 302.3 s vs 315.9 s. **keep off (i100 default unchanged).** Each of these switches can still be turned on per run (`--extra --f2-readiness-probes`, …).

### Held-out eval panel (worlds 1000–1039, mixed classes)

| Arm | solved 30 min | solved 45 min | RMST 45 (s) | late-half area 45 | median (s) |
|---|---:|---:|---:|---:|---:|
| i054a | 76/80 | 76/80 | 615.6 | 0.050 | 502.5 |
| i082 | 74/80 | 76/80 | 621.7 | 0.063 | 356.8 |
| i085 | 73/80 | 76/80 | 616.8 | 0.071 | 359.4 |
| i085-tail | 74/80 | 76/80 | 555.7 | 0.065 | 341.9 |
| final01 | 76/80 | 76/80 | 517.8 | 0.053 | 305.3 |
| final01-noclinic | 75/80 | 76/80 | 588.8 | 0.064 | 322.5 |
| final01-nohint | 76/80 | 76/80 | 521.6 | 0.052 | 314.1 |
| final01-noprior | 76/80 | 76/80 | 547.4 | 0.054 | 314.1 |
| final01-i081 | 76/80 | 76/80 | 509.2 | 0.052 | 309.4 |

Per class at 45 min: solved / RMST (s)

| Arm | easy | tactical | resource | root_act2 | root_act1 | mixed | unsolvable |
|---|---:|---:|---:|---:|---:|---:|---:|
| i054a | 32/32 · 376 | 12/12 · 166 | 12/12 · 579 | 10/10 · 758 | 6/6 · 1007 | 4/4 · 964 | 0/4 · 2700 |
| i082 | 32/32 · 355 | 12/12 · 125 | 12/12 · 425 | 10/10 · 849 | 6/6 · 1348 | 4/4 · 1104 | 0/4 · 2700 |
| i085 | 32/32 · 358 | 12/12 · 124 | 12/12 · 405 | 10/10 · 930 | 6/6 · 1131 | 4/4 · 1164 | 0/4 · 2700 |
| i085-tail | 32/32 · 298 | 12/12 · 127 | 12/12 · 364 | 10/10 · 665 | 6/6 · 1197 | 4/4 · 1099 | 0/4 · 2700 |
| final01 | 32/32 · 316 | 12/12 · 114 | 12/12 · 418 | 10/10 · 583 | 6/6 · 946 | 4/4 · 657 | 0/4 · 2700 |
| final01-noclinic | 32/32 · 305 | 12/12 · 114 | 12/12 · 523 | 10/10 · 742 | 6/6 · 1277 | 4/4 · 956 | 0/4 · 2700 |
| final01-nohint | 32/32 · 316 | 12/12 · 114 | 12/12 · 376 | 10/10 · 650 | 6/6 · 916 | 4/4 · 732 | 0/4 · 2700 |
| final01-noprior | 32/32 · 311 | 12/12 · 114 | 12/12 · 428 | 10/10 · 687 | 6/6 · 1070 | 4/4 · 810 | 0/4 · 2700 |
| final01-i081 | 32/32 · 302 | 12/12 · 114 | 12/12 · 418 | 10/10 · 664 | 6/6 · 787 | 4/4 · 631 | 0/4 · 2700 |

Paired restricted-time difference at 45 min vs **i082** (negative = faster; 95% bootstrap over worlds)

| Arm | mean (s) | 95% interval | faster / slower pairs |
|---|---:|---|---:|
| i054a | -6.1 | [-72.6, +66.4] | 41 / 34 |
| i085 | -4.9 | [-40.8, +31.1] | 28 / 31 |
| i085-tail | -66.0 | [-115.1, -14.6] | 35 / 28 |
| final01 | -103.8 | [-172.5, -41.8] | 52 / 10 |
| final01-noclinic | -32.9 | [-91.8, +30.8] | 48 / 14 |
| final01-nohint | -100.1 | [-166.9, -41.4] | 51 / 11 |
| final01-noprior | -74.3 | [-125.8, -28.3] | 50 / 12 |
| final01-i081 | -112.4 | [-178.2, -48.8] | 48 / 14 |

Paired restricted-time difference at 45 min vs **i054a** (negative = faster; 95% bootstrap over worlds)

| Arm | mean (s) | 95% interval | faster / slower pairs |
|---|---:|---|---:|
| i082 | +6.1 | [-66.4, +72.6] | 34 / 41 |
| i085 | +1.2 | [-79.9, +80.3] | 37 / 38 |
| i085-tail | -59.9 | [-132.9, +15.8] | 45 / 30 |
| final01 | -97.8 | [-161.4, -39.9] | 43 / 32 |
| final01-noclinic | -26.8 | [-100.9, +46.3] | 40 / 35 |
| final01-nohint | -94.0 | [-153.1, -41.0] | 44 / 31 |
| final01-noprior | -68.2 | [-125.6, -18.6] | 41 / 34 |
| final01-i081 | -106.4 | [-175.3, -40.5] | 46 / 29 |

Paired restricted-time difference at 45 min vs **final01** (negative = faster; 95% bootstrap over worlds)

| Arm | mean (s) | 95% interval | faster / slower pairs |
|---|---:|---|---:|
| i054a | +97.8 | [+39.9, +161.4] | 32 / 43 |
| i082 | +103.8 | [+41.8, +172.5] | 10 / 52 |
| i085 | +99.0 | [+20.1, +186.6] | 11 / 50 |
| i085-tail | +37.8 | [-30.8, +114.6] | 26 / 37 |
| final01-noclinic | +70.9 | [+24.9, +120.9] | 9 / 25 |
| final01-nohint | +3.8 | [-17.9, +26.3] | 8 / 13 |
| final01-noprior | +29.6 | [-3.8, +72.0] | 10 / 17 |
| final01-i081 | -8.6 | [-46.6, +31.3] | 15 / 26 |

### Held-out evalhard panel (worlds 4000–4039, long tail)

| Arm | solved 30 min | solved 45 min | RMST 45 (s) | late-half area 45 | median (s) |
|---|---:|---:|---:|---:|---:|
| i054a | 80/80 | 80/80 | 702.0 | 0.005 | 685.2 |
| i082 | 71/80 | 80/80 | 870.0 | 0.078 | 779.2 |
| i085 | 69/80 | 79/80 | 893.2 | 0.091 | 800.0 |
| i085-tail | 77/80 | 80/80 | 727.2 | 0.038 | 542.9 |
| final01 | 78/80 | 80/80 | 696.6 | 0.029 | 631.4 |
| final01-noclinic | 79/80 | 80/80 | 746.9 | 0.028 | 725.5 |
| final01-nohint | 79/80 | 80/80 | 699.1 | 0.019 | 643.4 |
| final01-noprior | 79/80 | 79/80 | 686.0 | 0.019 | 647.9 |
| final01-i081 | 76/80 | 78/80 | 740.2 | 0.049 | 644.4 |

Per class at 45 min: solved / RMST (s)

| Arm | tactical | resource | root_act2 | root_act1 | mixed |
|---|---:|---:|---:|---:|---:|
| i054a | 16/16 · 323 | 16/16 · 767 | 16/16 · 604 | 16/16 · 1082 | 16/16 · 734 |
| i082 | 16/16 · 248 | 16/16 · 968 | 16/16 · 662 | 16/16 · 1484 | 16/16 · 988 |
| i085 | 16/16 · 240 | 15/16 · 988 | 16/16 · 662 | 16/16 · 1540 | 16/16 · 1036 |
| i085-tail | 16/16 · 220 | 16/16 · 607 | 16/16 · 607 | 16/16 · 1032 | 16/16 · 1170 |
| final01 | 16/16 · 234 | 16/16 · 874 | 16/16 · 543 | 16/16 · 1040 | 16/16 · 792 |
| final01-noclinic | 16/16 · 234 | 16/16 · 714 | 16/16 · 590 | 16/16 · 1266 | 16/16 · 932 |
| final01-nohint | 16/16 · 234 | 16/16 · 813 | 16/16 · 555 | 16/16 · 1008 | 16/16 · 886 |
| final01-noprior | 16/16 · 234 | 15/16 · 796 | 16/16 · 591 | 16/16 · 977 | 16/16 · 832 |
| final01-i081 | 16/16 · 236 | 14/16 · 1033 | 16/16 · 558 | 16/16 · 1078 | 16/16 · 795 |

Paired restricted-time difference at 45 min vs **i082** (negative = faster; 95% bootstrap over worlds)

| Arm | mean (s) | 95% interval | faster / slower pairs |
|---|---:|---|---:|
| i054a | -168.0 | [-256.3, -85.4] | 50 / 28 |
| i085 | +23.2 | [-26.2, +76.6] | 28 / 42 |
| i085-tail | -142.8 | [-272.8, -20.1] | 46 / 31 |
| final01 | -173.4 | [-259.5, -100.2] | 58 / 15 |
| final01-noclinic | -123.1 | [-196.8, -54.4] | 58 / 15 |
| final01-nohint | -170.9 | [-256.5, -93.9] | 59 / 14 |
| final01-noprior | -184.0 | [-279.0, -100.1] | 59 / 14 |
| final01-i081 | -129.8 | [-239.4, -35.3] | 55 / 18 |

Paired restricted-time difference at 45 min vs **i054a** (negative = faster; 95% bootstrap over worlds)

| Arm | mean (s) | 95% interval | faster / slower pairs |
|---|---:|---|---:|
| i082 | +168.0 | [+85.4, +256.3] | 28 / 50 |
| i085 | +191.2 | [+99.7, +291.5] | 28 / 50 |
| i085-tail | +25.2 | [-93.9, +145.6] | 45 / 33 |
| final01 | -5.4 | [-90.7, +78.2] | 42 / 35 |
| final01-noclinic | +44.9 | [-24.5, +120.8] | 34 / 43 |
| final01-nohint | -2.9 | [-74.7, +68.0] | 40 / 37 |
| final01-noprior | -16.0 | [-93.9, +61.7] | 40 / 37 |
| final01-i081 | +38.2 | [-60.9, +139.6] | 41 / 36 |

Paired restricted-time difference at 45 min vs **final01** (negative = faster; 95% bootstrap over worlds)

| Arm | mean (s) | 95% interval | faster / slower pairs |
|---|---:|---|---:|
| i054a | +5.4 | [-78.2, +90.7] | 35 / 42 |
| i082 | +173.4 | [+100.2, +259.5] | 15 / 58 |
| i085 | +196.6 | [+112.9, +283.2] | 14 / 58 |
| i085-tail | +30.6 | [-85.0, +144.9] | 39 / 38 |
| final01-noclinic | +50.3 | [-37.6, +147.2] | 27 / 32 |
| final01-nohint | +2.5 | [-34.8, +39.7] | 20 / 14 |
| final01-noprior | -10.6 | [-47.1, +25.4] | 27 / 23 |
| final01-i081 | +43.6 | [-29.8, +110.5] | 22 / 39 |

## Development

Every design decision used the development panels (worlds 2000+ mixed, 3000+ long tail). Their history, including the designs that were rejected, is in [evidence/README.md](evidence/README.md); the reports are in [evidence/sim/dev](evidence/sim/dev/).

## Native panel

[native-panel.json](native-panel.json) lists the stratified seeds (i054a unsolved / slow / fast), solver seeds, arms and the combat-start-selection regression seed 1899537279. Commands: [docs/I100.md §7](../../docs/I100.md#7-本机-windows-原生验证).
