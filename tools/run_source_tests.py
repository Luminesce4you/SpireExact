"""Run the source-only stdlib contracts without an installed game/CombatSolver.

The main tools/run_tests.py intentionally runs the full suite. Source checkouts
exclude vendor/CombatSolver, so its patch-contract test cannot be run until
setup_source.py prepares that vendor tree. Every other discovered test runs.
When the vendor source exists, this runner runs the full discovered suite.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

VENDOR_MIRROR = ROOT / "vendor/CombatSolver/src/Engine/InCombat/Mirrors/HookMirrors.cs"
DEPENDENCY_BOUND_MODULES = {"test_solver_patches"}


def iter_cases(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from iter_cases(test)
        else:
            yield test


def main():
    discovered = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    all_cases = list(iter_cases(discovered))
    if not all_cases:
        raise SystemExit("No tests discovered")
    source_only = not VENDOR_MIRROR.exists()
    excluded = [test for test in all_cases
                if source_only and type(test).__module__ in DEPENDENCY_BOUND_MODULES]
    tests = [test for test in all_cases if test not in excluded]
    if source_only:
        print(f"Source checkout: skipping {len(excluded)} vendor-only test cases "
              "(tests/test_solver_patches.py requires a prepared CombatSolver checkout).",
              flush=True)
    if source_only and not excluded:
        raise SystemExit("Expected vendor-only test selection was not discovered")
    result = unittest.TextTestRunner(verbosity=1).run(unittest.TestSuite(tests))
    print(f"Source suite: {result.testsRun} run, {len(result.skipped)} skipped, "
          f"{len(excluded)} vendor-only excluded; full native validation: not run", flush=True)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
