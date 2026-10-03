"""Native one-action counterfactuals; no handwritten card/event effect rules."""
from ..canonical import canonical,ContractError
from .native_tree import observed_delta

class NativeTransitionOracle:
    def __init__(self,evaluator):self.evaluator=evaluator
    def initial(self):
        request=self.evaluator.request([]);request['stop_at_strategic_decision']=True
        return self.evaluator.batch([{'kind':'transition_initial','request':request}])[0][1]
    def apply(self,state,action):
        if state.get('status')!='DECISION':raise ContractError('transition requires a native decision state')
        if not any(canonical(a)==canonical(action) for a in state.get('actions',[])):
            raise ContractError('action is absent from the actual native menu')
        prefix=state['trace']+[action]
        request=self.evaluator.request(prefix);request['stop_at_strategic_decision']=True
        result=self.evaluator.batch([{'kind':'transition_apply','request':request}])[0][1]
        observed=result.get('status') in ('DECISION','TERMINAL') and bool(result.get('observation'))
        return {'state':result,'observed_delta':observed_delta(state.get('observation') or {},result['observation']) if observed else None,
                'input_prefix':prefix,'effects_are_native':observed,
                'pending_native_card_selection':result.get('phase')=='select_cards',
                'execution_boundary':result.get('phase'),
                'state_identity_is_observation_projection':False}
