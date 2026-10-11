"""Compatibility contracts for the mechanically extracted i054a modules."""
import hashlib
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from spire_exact.canonical import ContractError
from spire_exact.planning import evaluation, search, search_config


class RefactorBoundaryTests(unittest.TestCase):
    def test_historical_imports_remain_aliases(self):
        self.assertIs(search.SearchConfig, search_config.SearchConfig)
        self.assertIs(search.Evaluator, evaluation.Evaluator)
        self.assertTrue(callable(search.solve))
        self.assertTrue(callable(search.near_miss))

    def test_frozen_configuration_and_defaults(self):
        config = search.SearchConfig()
        self.assertEqual(config.scheduler, "weighted")
        self.assertEqual(config.gate_retry_percent, 50)
        self.assertEqual(config.gate_probe_schedule, ())
        with self.assertRaises(FrozenInstanceError):
            config.seconds = 42

    def test_request_seed_scaling_and_advisor_copy(self):
        ctx = {"seed": "42", "character": "IRONCLAD", "ascension": 10, "unlocks": "all"}
        advisor = {"budget_ms": 10, "boss_budget_ms": 20, "nodes": 300}
        ev = search.Evaluator(None, ctx, Path("."), search.SearchConfig(solver_seed=271828, low_io=True), advisor)
        req = ev.request([], policy=3, advisor_scale=2, advisor_patch={"profile": "Low"})
        expected = int.from_bytes(hashlib.sha256(b"271828:3").digest()[:4], "big") & 0x7fffffff
        self.assertEqual(req["policy_seed"], expected)
        self.assertEqual(req["advisor"]["nodes"], 600)
        self.assertEqual(req["advisor"]["budget_ms"], 20)
        self.assertEqual(req["advisor"]["boss_budget_ms"], 40)
        self.assertTrue(req["advisor"]["fix_consumed_block_compensation"])
        self.assertTrue(req["advisor"]["quiet_diagnostics"])
        self.assertEqual(req["advisor"]["profile"], "Low")
        self.assertEqual(advisor, {"budget_ms": 10, "boss_budget_ms": 20, "nodes": 300})

    def test_scoped_requests_cannot_escape_prefix(self):
        ctx = {"seed": "42", "character": "IRONCLAD", "ascension": 10, "unlocks": "all"}
        prefix = [{"kind": "card_reward", "card": "GOOD"}]
        ev = search.Evaluator(None, ctx, Path("."), search.SearchConfig(), scope_prefix=prefix, stop_floor=30)
        self.assertEqual(ev.request(prefix, horizon=50)["stop_at_floor"], 30)
        with self.assertRaises(ContractError):
            ev.request([])


if __name__ == "__main__":
    unittest.main()
