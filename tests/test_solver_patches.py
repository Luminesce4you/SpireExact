"""Reject unsafe upstream drift before producing a native optimization variant."""
from pathlib import Path
import unittest
from tools.solver_patches import lazy_hook_contexts


class SolverPatchContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (Path(__file__).resolve().parents[1]/
            'vendor/CombatSolver/src/Engine/InCombat/Mirrors/HookMirrors.cs').read_text(encoding='utf-8')

    def test_does_not_defer_side_effecting_initializer(self):
        mutant = self.source.replace('Amount = amount,', 'Amount = Math.Abs(amount),', 1)
        self.assertNotEqual(mutant, self.source)
        with self.assertRaisesRegex(ValueError, 'pure argument copy'):
            lazy_hook_contexts(mutant)

    def test_does_not_silently_skip_renamed_upstream_method(self):
        mutant = self.source.replace('public static void BeforeBlockGained(',
                                     'public static void RenamedBeforeBlockGained(', 1)
        with self.assertRaisesRegex(ValueError, 'method changed'):
            lazy_hook_contexts(mutant)

    def test_does_not_defer_context_used_before_enumeration(self):
        needle = 'foreach (var listener in IterateCombatHookListeners(simulator, MirroredHookMask.BeforeBlockGained))'
        mutant = self.source.replace(needle, 'Consume(context);\n        '+needle, 1)
        with self.assertRaisesRegex(ValueError, 'before first listener'):
            lazy_hook_contexts(mutant)
