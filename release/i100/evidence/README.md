# i100 evidence

| File | Content |
|---|---|
| `tests.json`, `unittest.log` | Full Python suite on the release source (counts in the JSON; the only error is `test_solver_patches` setup, which needs `vendor/CombatSolver` from `tools/setup_source.py`; two skips are Windows-only). |
| `compatibility.json` | `tools/check_i085_compat.py` against an extracted i082 tree (4c5823a): legacy / i075-final / i081 / i082 resolve unchanged when selected (additive keys only), i082 launcher argv equal, 18 fixture paths / 1,440 requests identical. Native differences are exactly the two expected files of the combat-boundary fix, listed with both digests. |
| `freeze.json` | Source identity frozen before the held-out panels ran, and the changes made after it. |
| `sim/eval.jsonl`, `sim/evalhard.jsonl` | Held-out panels, one row per (world, solver seed, arm): `eval` worlds 1000–1039 (mixed classes), `evalhard` worlds 4000–4039 (long tail); solver seeds 271828 and 314159; 45 simulated minutes. |
| `sim/eval.json`, `sim/evalhard.json` | `tools/final01_sim_study.py report`: solved at 30/45 minutes, restricted mean time, late-half area, per class, paired differences against i082, i054a and final01 with 95% bootstrap intervals that resample worlds. |
| `sim/pooled-heldout.json` | Both held-out panels pooled (160 runs per arm), paired differences, and the default-configuration decision with its rule. |
| `sim/recheck-final-code.log` | 12 held-out runs repeated with the release code (after the combat-boundary fix and the i100 rename): every result identical. |
| `sim/dev/` | Development panels (worlds 2000+ mixed, 3000+ long tail), used for every design decision. |

In the simulation tools the i100 configuration keeps its research name `final01`; `final01-*` are its ablations.

## Development history (dev panels only)

The development runs used the code as it was at the time; arm names refer to the arm table of that time.

| Run | Code state | What it decided |
|---|---|---|
| `dev-pilot1`, `dev-pilot2` | first clinic versions (pilot2 re-ran only `final01` after a clinic change; i054a/i082 rows carried over) | clinic direction right on mixed worlds; long-tail panel needed |
| `devhard-pilot2` | young-stem promotion on | promotion concentrated picks on stems that die before their gate (final01 37/40 at 45 min, slowest arm) → off by default |
| `devhard-pilot3` | promotion off; ablations of the inherited i081/i082 machinery | inherited synthetic F2 machinery and paired tables cost time next to the clinic (796 s vs 720 s without them) |
| `devhard-pilot4` | `final01` = i081/i082 synthetic F2 machinery on; `final01-lean` = off (restarted once; resumable rows kept) | lean faster (RMST 660 vs 723 s; 33 faster / 4 slower pairs vs i082) → lean became the configuration |
| `dev-pilot5` | final source, all four arms run fresh | easy class: 384 s vs i082 407 s vs i054a 419 s; no further change |

Other corrections made from dev-panel diagnostics: a raw HP-slope "depth" signal made verdicts flip between ROOT and UNDECIDED and was replaced by the full-HP projection plus hysteresis; sibling escalation to an earlier act was made lighter.

Until devhard-pilot4 the simulator gave Coordinator retry members a +0.5 search-level bonus. Only arms using the `escalate` preset (the i054a arm) were affected, in their favour; the i054a/i082 rows of devhard-pilot2–4 were carried over from devhard-pilot2. dev-pilot5 and the held-out panels were run after the bonus was removed.

Simulated seconds come from the simulator's cost model of a 7-worker pool.
