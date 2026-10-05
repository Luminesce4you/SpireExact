"""Source contract of the advisor's combat boundary (no .NET here).

A combat-start effect (for example a relic that exhausts a card from hand at
the start of every turn) asks for a selection before the first combat decision
of a fight. The advisor must switch to the new combat at that selection: the
previous combat's queued choices are stale, and a strict F1 winner deployment
must end when final F2 begins, even when F2 opens with such a selection.
Before this contract the switch happened only at the first `Suggest`, so the
F2 opening selection was judged against the F1 route and rejected
(F1_WINNER_DEPLOYMENT_MISMATCH:unplanned_nested_selection).
"""
import re
import unittest
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1] / 'native' / 'SpireNativeHost'


def method(source, signature):
    start = source.index(signature)
    depth, index = 0, source.index('{', start)
    for position in range(index, len(source)):
        if source[position] == '{':
            depth += 1
        elif source[position] == '}':
            depth -= 1
            if depth == 0:
                return source[start:position + 1]
    raise AssertionError('unbalanced method: ' + signature)


class CombatBoundaryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.advisor = (NATIVE / 'BeamAdvisor.cs').read_text(encoding='utf-8')
        cls.replay = (NATIVE / 'CampaignReplay.cs').read_text(encoding='utf-8')

    def test_every_combat_switch_goes_through_enter_combat(self):
        enter = method(self.advisor, 'void EnterCombat(CombatState state,string reason)')
        self.assertIn('Invalidate(reason);lastCombat=state;lastTurn=-1;', enter)
        self.assertIn('combat_instance_changed', enter)
        # lastCombat is assigned nowhere else, so a combat switch always drops
        # queued choices and ends a strict F1 deployment of the previous combat.
        self.assertEqual(len(re.findall(r'\blastCombat\s*=(?!=)', self.advisor)), 1)

    def test_selection_before_first_decision_enters_the_new_combat_first(self):
        selection = method(self.advisor, 'public JsonNode? SuggestSelection(CombatState? state,')
        switch = selection.index('if(state!=null&&state!=lastCombat)')
        self.assertLess(switch, selection.index('EnterCombat(state,"new_combat_selection")'))
        self.assertLess(selection.index('EnterCombat(state,"new_combat_selection")'),
                        selection.index('pendingChoices.TryDequeue'))
        self.assertLess(selection.index('EnterCombat(state,"new_combat_selection")'),
                        selection.index('RequireNoForcedF1Fallback'))

    def test_suggest_uses_the_same_switch(self):
        suggest = method(self.advisor, 'public JsonNode? Suggest(CombatState state,Player player,JsonNode[] legal)')
        self.assertIn('if(state!=lastCombat)EnterCombat(state,"new_combat");', suggest)

    def test_host_passes_the_live_combat_to_selection_advice(self):
        self.assertEqual(self.replay.count('advisor.SuggestSelection('), 1)
        self.assertIn('advisor.SuggestSelection(CombatManager.Instance.DebugOnlyGetState(),selectionCards,', self.replay)
        # Strict F1 deployment is still rejected by any fallback inside F1 itself.
        self.assertIn('bool StrictF1 => forcedF1Loaded && ReferenceEquals(lastCombat, forcedF1Combat);', self.advisor)


if __name__ == '__main__':
    unittest.main()
