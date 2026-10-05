# i085-final01 evidence

Nothing here is native or game evidence. No .NET build, STS2 rollout or native
verified win was produced in this work.

| File | What it is |
|---|---|
| `tests.json`, `unittest.log` | Full Python suite on the final source: 972 run, 969 passed, 0 failures, 2 errors, 2 skips. The two errors are the pre-existing baseline ones (dashboard test expecting 180 public minutes; `vendor/CombatSolver` source absent here); the skips are Windows-only. |
| `compatibility.json` | `tools/check_i085_compat.py` against an extracted i082 tree (4c5823a): old profiles (legacy / i075-final / i081 / i082) resolve unchanged except additive disabled keys, i082 launcher argv equal, 18 fixture paths / 1,440 requests identical, native/proof/lock files byte-identical. Absolute paths replaced by labels. |
| `freeze.json` | Source identity frozen before the held-out panels ran (`executable_source_hash`, per-file sha256 of every changed Python file). After launch only `report()` of `tools/final01_sim_study.py` changed (bootstrap intervals, several references); `run()`, the simulator and the planner did not. |
| `sim/eval.jsonl`, `sim/evalhard.jsonl` | Held-out synthetic panels, one row per (world, solver seed, arm), run once on the frozen source. `eval`: worlds 1000–1039, mixed classes; `evalhard`: worlds 4000–4039, long-tail classes. |
| `sim/eval.json`, `sim/evalhard.json` | `tools/final01_sim_study.py report` output: solved at 30/45 simulated minutes, restricted mean time, late-half area, per class, paired differences against i082, i054a and final01 with 95% bootstrap intervals that resample worlds. |
| `sim/dev/` | Development panels (worlds 2000+ mixed, 3000+ long-tail), used for every design decision. See below. |

## Development history (dev panels only)

The development runs used the code as it was at the time; arm names refer to
the arm table of that time, not to the final one.

| Run | Code state | What it decided |
|---|---|---|
| `dev-pilot1`, `dev-pilot2` | first clinic versions (pilot2 re-ran only `final01` after a clinic change; i054a/i082 rows carried over) | clinic direction looked right on mixed worlds; long-tail panel needed |
| `devhard-pilot2` | young-stem promotion on | promotion concentrated picks on stems that die before their gate (final01 37/40 at 45 min, slowest arm) → off by default |
| `devhard-pilot3` | promotion off; ablations of the inherited i081/i082 machinery | inherited synthetic F2 machinery and paired tables cost time next to the clinic (final01 796 s vs 720 s without the i081 machinery) |
| `devhard-pilot4` | `final01` = i081/i082 synthetic F2 machinery on; `final01-lean` = off (restarted once; resumable rows kept) | lean faster (RMST 660 vs 723 s; 33 faster / 4 slower pairs vs i082) → lean became `final01` |
| `dev-pilot5` | final source, all four arms run fresh | easy-class check: final01 384 s vs i082 407 s vs i054a 419 s; no easy regression; no further change |

Other corrections made from dev-panel diagnostics (the exact pilot is not
recorded): a raw HP-slope "depth" signal made verdicts flip between ROOT and
UNDECIDED and was replaced by the full-HP projection plus hysteresis; sibling
escalation to an earlier act was made lighter.

Until devhard-pilot4 the simulator gave Coordinator retry members a +0.5
search-level bonus. Only arms using the `escalate` preset (the i054a arm) were
affected, in their favour; the i054a/i082 rows of devhard-pilot2–4 were carried
over from devhard-pilot2. dev-pilot5 and the held-out panels were run fresh
after the bonus was removed.

Simulated seconds are a cost model of a 7-worker pool, not native seconds.
