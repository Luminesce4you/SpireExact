import unittest
from spire_exact.planning.budget_audit import clock_gate_audit


class ClockGateAuditTests(unittest.TestCase):
    def test_explicit_boundary_is_never_overridden_by_timing(self):
        self.assertFalse(clock_gate_audit({'budget_ms': 600000, 'wall_us': 100, 'time_boundary': True})['clock_gates_excluded'])

    def test_short_search_excludes_both_global_and_eight_layer_clocks(self):
        self.assertTrue(clock_gate_audit({'budget_ms': 600000, 'wall_us': 60000000, 'time_boundary': False})['clock_gates_excluded'])

    def test_near_local_slice_is_unproven_even_without_reported_hit(self):
        audit = clock_gate_audit({'budget_ms': 600000, 'wall_us': 70000000, 'time_boundary': False})
        self.assertFalse(audit['clock_gates_excluded'])
        self.assertFalse(audit['reported_time_boundary'])

    def test_missing_or_invalid_telemetry_never_certifies(self):
        for row in ({}, {'budget_ms': 600000, 'wall_us': 1},
                    {'budget_ms': True, 'wall_us': 0, 'time_boundary': False},
                    {'budget_ms': 600000, 'wall_us': float('nan'), 'time_boundary': False}):
            self.assertFalse(clock_gate_audit(row)['clock_gates_excluded'])
