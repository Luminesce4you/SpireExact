import copy
import random
import unittest
from unittest.mock import patch
from pathlib import Path
from spire_exact.canonical import ContractError, UnsupportedSemantic, canonical, clone, loads
from spire_exact.core import Limits, Transition, solve
from spire_exact.symbolic import GraphModel, SymbolicModel, effects, expression
from spire_exact.verify import replay_trace, verify_graph

ROOT = Path(__file__).resolve().parents[1]

def graph(states, root='r'):
    return GraphModel({'schema':'spire-explicit-model/v1','model_id':'test','root':root,'states':states})

def edge(action, target): return {'action':{'id':action},'to':target}

class CoreTests(unittest.TestCase):
    def simple(self):
        return graph({'r':{'edges':[edge('greedy','bad'),edge('setup','x'),edge('wait','r')]},
                      'bad':{'value':[1,3]},'x':{'edges':[edge('finish','best')]},'best':{'value':[1,9]}})
    def test_finds_non_greedy_optimum(self):
        r = solve(self.simple())
        self.assertEqual(r.result['best_value'],[1,9])
        self.assertEqual([s['action']['id'] for s in r.result['trace']],['setup','finish'])
        self.assertTrue(verify_graph(self.simple(),r.graph)['verified'])
    def test_exact_cycle_terminates(self):
        r = solve(self.simple())
        self.assertEqual(r.result['statistics']['states'],4)
        self.assertEqual(r.result['statistics']['duplicate_successors'],1)
    def test_all_distinct_exits_retained(self):
        self.assertEqual(len(solve(self.simple()).result['terminal_states']),2)
    def test_lexicographic_not_weighted(self):
        r = solve(graph({'r':{'edges':[edge('a','a'),edge('b','b')]},
                         'a':{'value':[0,10**40]},'b':{'value':[1,-10**40]}}))
        self.assertEqual(r.result['best_value'],[1,-10**40])
    def test_root_terminal(self):
        m = graph({'r':{'value':[7]}})
        r = solve(m,Limits(max_states=1))
        self.assertEqual(r.result['trace'],[])
        self.assertTrue(verify_graph(m,r.graph)['verified'])
    def test_cycle_without_terminal(self):
        m = graph({'r':{'edges':[edge('wait','r')]}})
        r = solve(m)
        self.assertEqual(r.result['status'],'NO_TERMINAL_REACHABLE_IN_MODEL')
        self.assertFalse(r.result['proven_optimal_in_model'])
        self.assertTrue(verify_graph(m,r.graph)['verified'])
    def test_state_budget_never_proves(self):
        r = solve(self.simple(),Limits(max_states=2))
        self.assertEqual(r.result['status'],'INCOMPLETE')
        self.assertEqual(r.result['best_value'],[1,3])
        self.assertIsNone(r.result['upper_bound'])
        self.assertTrue(r.result['unresolved_nodes'])
    def test_expansion_budget(self):
        r = solve(self.simple(),Limits(max_expansions=1))
        self.assertEqual(r.result['stop_reason'],'expansion_budget')
        self.assertFalse(r.graph['closed'])
    def test_edge_budget(self):
        r = solve(self.simple(),Limits(max_edges=1))
        self.assertEqual(r.result['stop_reason'],'edge_budget')
        self.assertFalse(r.graph['closed'])
    def test_cancel(self):
        r = solve(self.simple(),cancel=lambda:True)
        self.assertEqual(r.result['stop_reason'],'cancelled')
        self.assertIsNone(r.result['best_value'])
    def test_time_budget(self):
        with patch('spire_exact.core.monotonic', side_effect=[0.0, 2.0, 2.0]):
            r = solve(self.simple(),Limits(max_seconds=1.0))
        self.assertEqual(r.result['status'],'INCOMPLETE')
        self.assertEqual(r.result['stop_reason'], 'time_budget')
    def test_unsupported_after_good_branch(self):
        m=graph({'r':{'edges':[edge('a','a')],'complete':False,'reason':'new card'},'a':{'value':[1]}})
        r=solve(m)
        self.assertEqual(r.result['best_value'],[1])
        self.assertEqual(r.result['unsupported'][0]['reason'],'new card')
        self.assertFalse(r.result['proven_optimal_in_model'])
    def test_duplicate_complete_action_is_error(self):
        m=graph({'r':{'edges':[edge('same','a'),edge('same','b')]},'a':{'value':[1]},'b':{'value':[2]}})
        with self.assertRaises(ContractError): solve(m)
    def test_objective_dimensions(self):
        m=graph({'r':{'edges':[edge('a','a'),edge('b','b')]},'a':{'value':[1]},'b':{'value':[1,2]}})
        with self.assertRaises(ContractError): solve(m)
    def test_terminal_input_mutation_is_error(self):
        class Bad(GraphModel):
            def terminal_value(self,state):
                state['oops']=1
                return None
        m=Bad({'schema':'spire-explicit-model/v1','model_id':'bad','root':'r','states':{'r':{}}})
        with self.assertRaises(ContractError): solve(m)
    def test_transition_input_mutation_is_error(self):
        m=self.simple()
        def mutate(state):
            state['oops']=1
            yield Transition({'id':'bad'},{'node':'bad'})
        m.transitions=mutate
        with self.assertRaises(ContractError): solve(m)
    def test_hidden_counter_is_not_merged(self):
        class Counter:
            def identity(self): return {'model':'counter'}
            def initial(self): return {'hidden_counter':0,'hp':10}
            def terminal_value(self,state): return None
            def transitions(self,state):
                yield Transition({'id':'tick'},{'hidden_counter':state['hidden_counter']+1,'hp':10})
        r=solve(Counter(),Limits(max_states=9))
        self.assertEqual(r.result['statistics']['states'],9)
        self.assertEqual(r.result['status'],'INCOMPLETE')
    def test_no_unsafe_hp_dominance(self):
        # Low-HP branch has a future threshold-only reward. The kernel never removes it.
        m=graph({'r':{'edges':[edge('hp10','high'),edge('hp2','low')]},
                 'high':{'edges':[edge('finish','a')]},'low':{'edges':[edge('threshold','b')]},
                 'a':{'value':[1,1]},'b':{'value':[1,100]}})
        self.assertEqual(solve(m).result['best_value'],[1,100])
    def test_replay(self):
        m=self.simple()
        result=solve(m)
        replay=replay_trace(m,[s['action'] for s in result.result['trace']])
        self.assertEqual(replay['terminal_value'],result.result['best_value'])
    def test_illegal_replay_rejected(self):
        with self.assertRaises(ContractError): replay_trace(self.simple(),[{'id':'invented'}])
    def test_trace_cannot_continue_after_terminal(self):
        with self.assertRaises(ContractError): replay_trace(self.simple(),[{'id':'greedy'},{'id':'wait'}])
    def test_invalid_limits(self):
        for kwargs in ({'max_states':0},{'max_edges':-1},{'max_expansions':True},
                       {'max_seconds':float('nan')},{'max_seconds':0}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError): Limits(**kwargs)

class IdentityTests(unittest.TestCase):
    def test_object_order_canonicalized(self): self.assertEqual(canonical({'a':1,'b':2}),canonical({'b':2,'a':1}))
    def test_array_order_preserved(self): self.assertNotEqual(canonical({'pile':[1,2]}),canonical({'pile':[2,1]}))
    def test_instance_identity_preserved(self): self.assertNotEqual(canonical({'card':{'id':1}}),canonical({'card':{'id':2}}))
    def test_rng_counters_preserved(self): self.assertNotEqual(canonical({'rng':{'state':1,'calls':2}}),canonical({'rng':{'state':1,'calls':3}}))
    def test_rng_state_preserved(self): self.assertNotEqual(canonical({'rng':{'state':1,'calls':2}}),canonical({'rng':{'state':2,'calls':2}}))
    def test_bool_not_integer(self): self.assertNotEqual(canonical({'x':1}),canonical({'x':True}))
    def test_floats_rejected(self):
        for x in (1.2,float('nan'),float('inf')):
            with self.subTest(x=x),self.assertRaises(ContractError): canonical({'x':x})
    def test_nonstring_keys_rejected(self):
        with self.assertRaises(ContractError): canonical({1:'x'})
    def test_duplicate_json_keys_rejected(self):
        with self.assertRaises(ContractError): loads('{"x":1,"x":2}')
    def test_fork_isolation(self):
        original={'hand':[{'id':1,'cost':2}]}
        fork=clone(original); fork['hand'][0]['cost']=0
        self.assertEqual(original['hand'][0]['cost'],2)

class ProofTests(unittest.TestCase):
    def setUp(self):
        self.model=CoreTests().simple()
        self.cert=solve(self.model).graph
    def test_missing_edge(self):
        self.cert['nodes'][0]['edges'].pop()
        with self.assertRaises(ContractError): verify_graph(self.model,self.cert)
    def test_wrong_value(self):
        self.cert['best_value']=[1,999]
        with self.assertRaises(ContractError): verify_graph(self.model,self.cert)
    def test_wrong_terminal_value(self):
        self.cert['nodes'][1]['value']=[1,999]
        with self.assertRaises(ContractError): verify_graph(self.model,self.cert)
    def test_wrong_identity(self):
        self.cert['identity']['backend_version']='wrong'
        with self.assertRaises(ContractError): verify_graph(self.model,self.cert)
    def test_wrong_root(self):
        self.cert['nodes'][0]['state']={'node':'bad'}
        with self.assertRaises(ContractError): verify_graph(self.model,self.cert)
    def test_partial_cert_rejected(self):
        cert=solve(self.model,Limits(max_states=2)).graph
        with self.assertRaises(ContractError): verify_graph(self.model,cert)
    def test_forged_closed_flag(self):
        cert=solve(self.model,Limits(max_states=2)).graph
        cert['closed']=True
        with self.assertRaises(ContractError): verify_graph(self.model,cert)
    def test_wrong_target(self):
        self.cert['nodes'][0]['edges'][0]['to']=0
        with self.assertRaises(ContractError): verify_graph(self.model,self.cert)
    def test_unreachable_fabricated_winner(self):
        self.model.graph['states']['injected']={'value':[1,999]}
        self.cert['nodes'].append({'id':4,'state':{'node':'injected'},'complete':True,'value':[1,999],'edges':[]})
        self.cert['best_terminal']=4; self.cert['best_value']=[1,999]
        with self.assertRaises(ContractError): verify_graph(self.model,self.cert)
    def test_duplicate_node(self):
        node=copy.deepcopy(self.cert['nodes'][1]); node['id']=len(self.cert['nodes'])
        self.cert['nodes'].append(node)
        with self.assertRaises(ContractError): verify_graph(self.model,self.cert)

class SymbolicTests(unittest.TestCase):
    def model(self, seed='42'): return SymbolicModel.from_file(str(ROOT/'examples/reference_campaign.json'),seed)
    def test_seed_reproducibility(self): self.assertEqual(self.model().initial(),self.model().initial())
    def test_seeds_have_distinct_context(self): self.assertNotEqual(self.model('42').initial(),self.model('43').initial())
    def test_random_stream_isolation(self):
        m=self.model(); initial=m.initial(); successor=next(m.transitions(initial)).state
        self.assertEqual(initial['rng']['rewards'],successor['rng']['rewards'])
        self.assertNotEqual(initial['rng']['encounters'],successor['rng']['encounters'])
    def test_reference_rng_is_not_claimed_native(self): self.assertFalse(self.model().identity()['is_native_sts2'])
    def test_unknown_effect_fails_closed(self):
        m=self.model(); m.spec['actions'][0]['effects']=[{'op':'invented'}]
        r=solve(m)
        self.assertEqual(r.result['status'],'INCOMPLETE')
        self.assertIn('invented',r.result['unsupported'][0]['reason'])
    def test_native_rng_not_silently_emulated(self):
        m=self.model(); s=m.initial(); s['rng']['encounters']['algorithm']='native-unknown'
        with self.assertRaises(UnsupportedSemantic): list(m.transitions(s))
    def test_reference_campaign_closed_and_replayed(self):
        m=self.model(); r=solve(m)
        self.assertEqual(r.result['best_value'],[1,12,0])
        route=[s['action']['id'] for s in r.result['trace']]
        self.assertGreater(route.index('use_potion'),route.index('camp_rest'))
        self.assertEqual(r.result['statistics']['states'],1451)
        self.assertTrue(verify_graph(m,r.graph)['verified'])
    def test_finite_parameters_enumerated_completely(self):
        spec={'schema':'spire-reference-ir/v1','model_id':'params','initial':{'done':0,'v':0},
              'terminal':{'eq':[{'get':'done'},1]},'objective':[{'get':'v'}],
              'actions':[{'id':'choose','parameters':{'v':[1,2,3]},'effects':[
                  {'op':'set','path':'v','value':{'param':'v'}},{'op':'set','path':'done','value':1}]}]}
        r=solve(SymbolicModel(spec))
        self.assertEqual(r.result['best_value'],[3]);self.assertEqual(len(r.result['terminal_states']),3)
    def test_no_implicit_truthiness(self):
        with self.assertRaises(ContractError): expression({'and':[1,True]}, {}, {})
    def test_no_float_arithmetic(self):
        with self.assertRaises(ContractError): expression({'add':[1,True]}, {}, {})
    def test_assert_is_not_silently_skipped(self):
        with self.assertRaises(ContractError): effects([{'op':'assert','condition':False}],{}, {})

class RandomGraphOracleTests(unittest.TestCase): pass

def make_random_test(seed):
    def test(self):
        rng=random.Random(seed)
        count=rng.randrange(8,40)
        states={}
        for i in range(count):
            if rng.random()<0.25:
                states[str(i)]={'value':[rng.randrange(2),rng.randrange(-20,100)]}
            else:
                states[str(i)]={'edges':[edge('a'+str(j),str(rng.randrange(count))) for j in range(rng.randrange(5))]}
        m=graph(states,'0')
        # Independent traversal over input graph, without calling solver or model.transitions.
        todo=['0'];seen=set();values=[]
        while todo:
            key=todo.pop()
            if key in seen: continue
            seen.add(key)
            node=states[key]
            if 'value' in node: values.append(tuple(node['value']))
            else: todo.extend(item['to'] for item in node['edges'])
        r=solve(m)
        expected=list(max(values)) if values else None
        self.assertEqual(r.result['best_value'],expected)
        self.assertEqual(r.result['statistics']['states'],len(seen))
        self.assertTrue(verify_graph(m,r.graph)['verified'])
    return test

for _seed in range(100):
    setattr(RandomGraphOracleTests, f'test_random_graph_{_seed:03}',make_random_test(_seed))
