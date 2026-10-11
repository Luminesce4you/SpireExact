# i054a-first refactor: boundaries, provenance, compatibility

## Why this branch exists

The native paired A/B study motivated treating **frozen i054a** as the
working baseline, rather than silently removing i100 features until the
configuration resembles i054a. The source parent is the public
`codex/holdout-01-release` commit `9a85d4d7ca5841bbc37fa8842a227b8545767d11`.
This is a new, isolated branch: neither that source release nor the
`i100-release` default branch is modified.

The original archived win certificates prove only their original native
binaries and runs. A refactor must earn fresh native validation. No speed,
win-rate, or historical-certificate transfer is asserted by this change.

## Module boundaries

| Module | Responsibility | Constraints |
| --- | --- | --- |
| `search_config.py` | Immutable `SearchConfig`, defaults and validation | Same dataclass fields, validation and serialized keys |
| `evaluation.py` | `Evaluator`: request policy seed, cache, checkpoints, streaming, proof replay | No search policy decisions; preserve recording order and JSON shape |
| `search.py` | `near_miss`, `solve` planning loop, result snapshots | Preserve old import entrypoints and deterministic proposal order |
| `archive.py`, `focus.py`, `gatemodel.py` | Existing policy and state components | No changes in this first boundary-only refactor |

The original `SearchConfig` and `Evaluator` class bodies and the original
`near_miss` and `solve` function bodies were moved without changing their
implementation. Only imports and module location changed. In particular,
existing tests patching `spire_exact.planning.search.Evaluator` still work,
because `solve` continues looking up that name in `search.py`.

## Review and validation

1. Compare `search.py` on this branch with the source commit above.
   Expected behavioral differences: **none**.
2. Run `python tools/run_source_tests.py` on a fresh source checkout. It runs
   the Python contracts but excludes the existing `test_solver_patches.py` only
   when its required `vendor/CombatSolver` checkout is missing.
3. After `tools/setup_source.py` has prepared the vendor tree, run the **full**
   suite with `python tools/run_tests.py` (including the upstream patch contract).
4. On supported Windows with the same pinned game, host and resource protocol,
   build using `tools/setup_source.py` and run a *new*, independently verified
   native seed panel. Do not compare inherited holdout certificates.
5. Before promoting a new release, compare canonical JSON request sequences
   and evaluation classifications against the frozen source under the same
   game/solver seeds and resource budget. A Windows native run is **not**
   performed in this source-only refactor.

## Next work, after regression validation

* Break the remaining `solve` orchestration into explicit immutable
  proposals, a scheduler adapter, an evidence accumulator, and a snapshot
  writer. Preserve the sequential order of `dispatch`, `collect`, and
  `absorb`; results may be time-sensitive.
* Replace ad-hoc untyped spec dictionaries with typed *internal* structures
  only behind a compatibility serializer, never change outgoing RPC JSON
  during a pure refactor.
* Add golden request-sequence fixtures and per-task accounting tests before
  changing the focus/repair algorithms.
* Keep new strategy research separate from refactor PRs. Future A/B experiments
  must pin both source trees and native identity.
