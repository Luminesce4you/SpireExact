"""Plugin capability boundary: no native game or CombatSolver required."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from spire_exact.planning.plugins.context import PluginContext


class FakeEvaluator:
    def __init__(self):
        self.advisor = {"gate_plans": {"Boss": {"members": [1]}}}
        self.calls = []

    def request(self, prefix, **kwargs):
        self.calls.append((prefix, kwargs))
        return {"history": prefix, **kwargs}


class PluginContextTests(unittest.TestCase):
    def make_context(self, *, stamp=None, model=None, scoped=False, component=False):
        evaluator = FakeEvaluator()
        search = SimpleNamespace(
            config=SimpleNamespace(lookahead_floors=99),
            pool=SimpleNamespace(stamp={"capabilities": ["native-cap"]} if stamp is None else stamp),
            ev=evaluator,
            models=model,
            scope_prefix=[1, 2] if scoped else None,
            component=object() if component else None,
            urgent=[],
            repairs=[],
        )
        return PluginContext(search), search

    def test_exposes_only_plugin_capabilities(self):
        ctx, search = self.make_context(scoped=True, component=True)
        self.assertIs(ctx.config, search.config)
        self.assertTrue(ctx.scoped)
        self.assertTrue(ctx.component_mode)
        self.assertFalse(ctx.has_gate_model)
        for forbidden in ("ev", "pool", "urgent", "repairs", "scheduler",
                          "frontier", "mechanisms", "_search"):
            with self.subTest(forbidden=forbidden):
                self.assertFalse(hasattr(ctx, forbidden))
        with self.assertRaises(AttributeError):
            ctx.urgent = []

    def test_detaches_advisor_and_capabilities(self):
        ctx, search = self.make_context()
        self.assertEqual(ctx.native_capabilities, frozenset({"native-cap"}))
        self.assertIsInstance(ctx.native_capabilities, frozenset)
        detached = ctx.advisor
        detached["gate_plans"]["Boss"]["members"].append(2)
        self.assertEqual(search.ev.advisor["gate_plans"]["Boss"]["members"], [1])
        search.pool.stamp["capabilities"].append("added")
        self.assertEqual(ctx.native_capabilities, frozenset({"native-cap", "added"}))
        self.assertEqual(ctx.advisor["gate_plans"]["Boss"]["members"], [1])

    def test_request_delegates_without_rewriting(self):
        ctx, search = self.make_context()
        history = [{"kind": "pick", "value": "X"}]
        kwargs = {"policy": 0, "horizon": 50, "advisor_patch": {"a": 1}}
        result = ctx.request(history, **kwargs)
        self.assertEqual(result, {"history": history, **kwargs})
        self.assertEqual(search.ev.calls, [(history, kwargs)])
        self.assertIs(search.ev.calls[0][0], history)

    def test_gate_model_reports_snapshot_values(self):
        model = SimpleNamespace(permuted_fits={"level": 3}, version=7)
        ctx, _ = self.make_context(model=model)
        self.assertTrue(ctx.has_gate_model)
        data = ctx.gate_model_metrics()
        self.assertEqual(data, {"permuted_fits": {"level": 3}, "model_version": 7})
        data["permuted_fits"]["level"] = 100
        self.assertEqual(model.permuted_fits["level"], 3)
        missing, _ = self.make_context()
        self.assertEqual(missing.gate_model_metrics(),
                         {"permuted_fits": {}, "model_version": None})

    def test_missing_native_stamp_is_safe(self):
        ctx, _ = self.make_context(stamp={})
        self.assertEqual(ctx.native_capabilities, frozenset())


if __name__ == "__main__":
    unittest.main()
