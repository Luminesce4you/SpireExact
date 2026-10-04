from types import SimpleNamespace
import unittest
from spire_exact.planning.final_defaults import FINAL_FEATURES, resolve_entry_defaults
from spire_exact.planning.search import SearchConfig


class FinalDefaultsTests(unittest.TestCase):
    def arguments(self,profile='i075-final',**overrides):
        return SimpleNamespace(feature_profile=profile,ascension=None,nodes=None,
                               **({key:None for key in FINAL_FEATURES}|overrides))

    def test_final_entry_enables_every_new_trigger_and_a(self):
        args=resolve_entry_defaults(self.arguments())
        self.assertTrue(all(getattr(args,key) for key in FINAL_FEATURES))
        self.assertEqual((args.ascension,args.nodes),(10,60000))
        config=SearchConfig.final()
        self.assertTrue(all(getattr(config,key) for key in FINAL_FEATURES))
        self.assertFalse(config.real_card_menu_probes) # unchanged separate B experiment

    def test_explicit_legacy_is_available_without_being_final_default(self):
        args=resolve_entry_defaults(self.arguments('legacy'))
        self.assertTrue(all(not getattr(args,key) for key in FINAL_FEATURES))
        self.assertEqual((args.ascension,args.nodes),(0,None))

    def test_explicit_controls_are_retained_and_shop_without_target_does_not_force_purchase(self):
        args=self.arguments(gold_shop_routes=False,memory_telemetry=False)
        args.ascension=10;args.nodes=70000
        args=resolve_entry_defaults(args)
        self.assertFalse(args.gold_shop_routes);self.assertFalse(args.memory_telemetry)
        self.assertTrue(args.shop_preparation)
        self.assertEqual(args.nodes,70000)
        self.assertFalse(SearchConfig.final(gold_shop_routes=False).gold_shop_routes)


if __name__=='__main__':unittest.main()
