"""Deferred work stays pending, with no change to native requests or priority."""
import copy
import unittest
from collections import deque
from spire_exact.canonical import canonical
from spire_exact.planning.archive import classify_failure
from spire_exact.planning.repairs import RepairQueue
from spire_exact.planning.search import SearchConfig, dispatch_blocked, take_dispatchable


class Scheduler:
    def dispatch_blocked(self, spec):return spec.get('source') == 'paused'
    def preparation_blocked(self, spec):return spec.get('preparation',{}).get('source') == 'paused'


class DispatchTests(unittest.TestCase):
    def test_deferred_urgent_keeps_request_and_order(self):
        paused={'kind':'macro_forge','preparation':{'source':'paused'},'request':{'history':[{'kind':'rest'}]}}
        ready={'kind':'deepen','source':'ready','request':{'history':[]}}
        queue=deque([paused,ready]);before=canonical(paused)
        self.assertIs(take_dispatchable(queue,Scheduler()),ready)
        self.assertEqual(list(queue),[paused]);self.assertEqual(canonical(paused),before)
        self.assertIsNone(take_dispatchable(queue,Scheduler()))
        self.assertEqual(list(queue),[paused])

    def test_repair_priority_is_preserved_among_eligible_items(self):
        queue=RepairQueue('gate')
        paused={'source':'paused','priority':(100,), 'request':{'history':[]}}
        early={'source':'ready','priority':(1,)};better={'source':'ready','priority':(2,)}
        for spec in (paused,early,better):queue.append(spec)
        blocked=lambda spec:dispatch_blocked(spec,Scheduler())
        self.assertIs(queue.popleft(blocked),better)
        self.assertIs(queue.popleft(blocked),early)
        self.assertIsNone(queue.popleft(blocked));self.assertEqual(queue.items,[paused])
        self.assertIs(queue.popleft(),paused)

    def test_off_dispatch_order_matches_original_queue(self):
        for mode in ('fifo','gate','deep_boss','deep_final_boss'):
            legacy=RepairQueue(mode);off=RepairQueue(mode)
            specs=[{'priority':(floor,), 'repair':{'floor':floor,'room':room,'deep':floor==49}}
                   for floor,room in ((8,'Elite'),(33,'Boss'),(49,'Boss'),(47,'Monster'))]
            for spec in specs:legacy.append(copy.deepcopy(spec));off.append(copy.deepcopy(spec))
            self.assertEqual([legacy.popleft() for _ in specs],
                             [off.popleft(lambda _:False) for _ in specs])

    def test_extended_requires_existing_threshold_and_focus(self):
        for kwargs in ({'focus_stall_extended':True},
                       {'focus_stall_extended':True,'focus_stall':32,'scheduler':'dfs'}):
            with self.assertRaises(ValueError):SearchConfig(**kwargs)
        self.assertFalse(SearchConfig().focus_stall_extended)
        self.assertFalse(SearchConfig().focus_stall_f2)
        self.assertFalse(SearchConfig().low_hp_routes_any_act)
        self.assertEqual(SearchConfig().forge_menu_dedup,'exact')
        SearchConfig(scheduler='focus',focus_stall=32,focus_stall_extended=True)

    def test_f2_is_separate_validated_opt_in(self):
        with self.assertRaises(ValueError):SearchConfig(scheduler='focus',focus_stall=32,focus_stall_f2=True)
        with self.assertRaises(ValueError):SearchConfig(focus_stall_f2=1)
        SearchConfig(scheduler='focus',focus_stall=32,focus_stall_extended=True,focus_stall_f2=True)

    def test_route_incompletion_does_not_override_real_terminal_death(self):
        result={'status':'TERMINAL','value':[0],'observation':{'hp':'0'},
                'map_route_result':{'entry_checked':True,'complete':False,
                                    'failure_reason':'MAP_ROUTE_DEATH_BEFORE_COMPLETION'}}
        self.assertEqual(classify_failure(result),'NATIVE_ROUTE_DEATH')
        for status,reason,expected in (('UNKNOWN','NATIVE_TASK_OUT_OF_MEMORY','RESOURCE_LIMIT'),
                                       ('BUDGET',None,'SEARCH_BUDGET'),
                                       ('UNSUPPORTED','CHECKPOINT_MISMATCH:map_route_graph_changed','RESTORE_OR_REPLAY_MISMATCH')):
            changed={**result,'status':status,'value':None,'reason':reason}
            self.assertEqual(classify_failure(changed),expected)
        # Metadata/text in an old unsupported record cannot alone establish a
        # terminal death or enter it into the new native-result accounting.
        self.assertEqual(classify_failure({**result,'status':'UNSUPPORTED','value':None}),
                         'MECHANISM_OR_HOST_GAP')


if __name__=='__main__':unittest.main()
