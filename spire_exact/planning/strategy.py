"""Heuristic macro scheduling. Feature signatures are never state equality.

Failure hypotheses select experiments, not proofs of cause or infeasibility.
Every proposal retains a complete executable history from the fresh initial run.
"""
from collections import Counter
from dataclasses import dataclass
from ..canonical import canonical
from .archive import classify_failure, failure_combat_prefix


def strategic_phase(e):
    phase=e.get('phase');obs=e.get('observation') or {}
    if phase=='select_cards':
        purpose=(obs.get('selection') or {}).get('purpose','Other')
        if obs.get('turn') is not None or obs.get('hand') is not None:return None
        return 'deck' if purpose in ('Upgrade','Remove','Transform','Duplicate','Obtain') else None
    return {'map':'route','card_reward':'deck','shop':'resources','event':'resources',
            'rest':'resources','treasure':'preparation'}.get(phase)


class FailureAnalyzer:
    @staticmethod
    def analyze(result):
        classification=classify_failure(result);obs=result.get('observation') or {}
        probe=failure_combat_prefix(result)
        entry=probe['entry_observation'] if probe else obs
        features=entry.get('strategic') or {}
        def n(k):return float(features.get(k) or 0)
        hypotheses=[]
        if classification in ('MECHANISM_OR_HOST_GAP','RESTORE_OR_REPLAY_MISMATCH','UNKNOWN'):
            hypotheses.append('execution_or_unsupported')
        elif classification=='NATIVE_ROUTE_DEATH':
            if entry.get('room')=='Boss' and n('scaling')+n('strength')<3:hypotheses.append('insufficient_scaling')
            if n('block_per_draw')<2.5:hypotheses.append('insufficient_block')
            if n('damage_per_draw')<5:hypotheses.append('insufficient_frontload')
            if n('deck_size')>22 and n('draw_per_card')<.3:hypotheses.append('deck_bloat')
            if float(entry.get('hp') or 0)<.4*max(1,float(entry.get('max_hp') or 1)):
                hypotheses.append('hp_resource_mismanagement')
            if not any(entry.get('potions',[])):hypotheses.append('insufficient_potion_preparation')
            if float(entry.get('gold') or 0)>150:hypotheses.append('unspent_shop_resources')
            counters=(result.get('advisor_metrics') or {}).get('counters') or {}
            if counters.get('fallback_count',0)>max(3,counters.get('advisor_hit',0)//5):
                hypotheses.append('advisor_execution_failure')
            hypotheses.append('combat_tactical_failure')
        return {'classification':classification,'act':int(obs.get('act') or 0),
                'floor':int(obs.get('floor') or 0),'room':entry.get('room'),
                'hypotheses':hypotheses,'diagnosis_is_heuristic':True}


def capability_signature(obs):
    s=obs.get('strategic') or {}
    # Coarse allocation bins only; no cache or exact-state deduplication uses this.
    scales={'attack_density':5,'defense_density':5,'damage_per_draw':.5,'block_per_draw':1,
            'draw_per_card':5,'energy_per_card':5,'mean_energy_cost':2,'strength':.5,
            'scaling':1,'aoe':.1,'weak':.5,'vulnerable':.5,'exhaust':.5,
            'exhaust_payoff':1,'upgrade_density':5,'burden':1}
    return tuple(int(float(s.get(k) or 0)*scale) for k,scale in scales.items())


def strategic_trace(result,rooms_only=False):
    rows=[]
    for action,e in zip(result.get('trace',[]),result.get('decision_evidence',[])):
        category=strategic_phase(e)
        if category is None or rooms_only and e.get('phase')!='map':continue
        obs=e.get('observation') or {}
        rows.append({'act':obs.get('act'),'floor':obs.get('floor'),'phase':e['phase'],'action':action})
    return canonical(rows)


@dataclass
class Branch:
    result:dict
    index:int
    action:dict
    category:str
    diagnosis:dict
    source_label:str
    order:int
    key:bytes


class StrategicScheduler:
    """Weighted fair queues across macro categories and room boundaries.

    Alternative requests are deduplicated by full canonical action prefix; this
    prevents scheduling the same experiment twice and makes no state claim.
    """
    weights={'route':.20,'deck':.25,'resources':.15,'failure':.20,'preparation':.10,'exploration':.10}
    def __init__(self):
        self.branches=[];self.seen=set();self.executed=set();self.submitted=set();self.counts=Counter()
        self.room_visits=Counter();self.source_visits=Counter();self.order=0
        self.generated=0;self.duplicates=0

    def add(self,result,label):
        trace=result.get('trace',[]);diagnosis=FailureAnalyzer.analyze(result)
        # These exact prefixes have already been executed by this candidate.
        # Do not propose returning to the original sibling on the next result.
        for i,e in enumerate(result.get('decision_evidence',[])[:len(trace)]):
            if strategic_phase(e):
                key=canonical(trace[:i+1]);self.seen.add(key);self.executed.add(key)
        room_phases=set()
        for i,e in enumerate(result.get('decision_evidence',[])[:len(trace)]):
            category=strategic_phase(e)
            if not category:continue
            obs=e.get('observation') or {};floor=int(obs.get('floor') or 0)
            # Reward navigation, potion disposal and repeated shop menus are not
            # new room experiments. A shop branch runs native continuation, so a
            # buy may naturally be followed by another buy or a card removal.
            room=(obs.get('act'),floor,e.get('phase'),(obs.get('selection') or {}).get('purpose'))
            if room in room_phases and e.get('phase')!='card_reward':continue
            room_phases.add(room)
            for action in e.get('available_actions',[]):
                if action.get('kind') in ('discard_potion','reward','rewards_skip'):continue
                if canonical(action)==canonical(trace[i]):continue
                key=canonical(trace[:i]+[action]);self.generated+=1
                if key in self.seen:self.duplicates+=1;continue
                self.seen.add(key);self.order+=1
                branch_category='preparation' if action.get('kind')=='use_potion' else category
                self.branches.append(Branch(result,i,action,branch_category,diagnosis,label,self.order,key))

    def _priority(self,b,category):
        e=b.result['decision_evidence'][b.index];obs=e.get('observation') or {}
        floor=int(obs.get('floor') or 0);act=int(obs.get('act') or 0)
        death_floor=b.diagnosis['floor'];death_act=b.diagnosis['act']
        # Boss preparation starts in the previous act or the current act's first
        # half, not the last reward menu. Prefer nearby resources for low HP.
        target=max(1,death_floor-(10 if b.diagnosis.get('room')=='Boss' else 5))
        if category=='exploration':target=1
        elif category=='preparation':target=max(1,death_floor-3)
        elif category=='failure' and 'hp_resource_mismanagement' in b.diagnosis['hypotheses']:
            target=max(1,death_floor-4)
        strategic_fit=0
        if category=='failure':
            hypotheses=b.diagnosis['hypotheses']
            if any(h in hypotheses for h in ('insufficient_scaling','insufficient_block','deck_bloat')):
                strategic_fit=int(b.category=='deck')
            if 'unspent_shop_resources' in hypotheses and e['phase']=='shop':strategic_fit+=2
        room=(act,floor,e['phase'])
        return (self.room_visits[room],-strategic_fit,abs(floor-target),
                self.source_visits[b.source_label],abs(act-death_act),b.order)

    def next(self):
        self.branches=[b for b in self.branches if b.key not in self.executed]
        if not self.branches:return None
        total=sum(self.counts.values())+1
        categories=sorted(self.weights,key=lambda c:self.weights[c]*total-self.counts[c],reverse=True)
        for category in categories:
            choices=[b for b in self.branches if category in ('failure','exploration') or b.category==category
                     or category=='preparation' and b.category=='resources']
            if not choices:continue
            b=min(choices,key=lambda b:self._priority(b,category));self.branches.remove(b)
            e=b.result['decision_evidence'][b.index];obs=e.get('observation') or {}
            prefix=b.result['trace'][:b.index]+[b.action];self.submitted.add(canonical(prefix))
            self.counts[category]+=1;self.source_visits[b.source_label]+=1
            self.room_visits[(obs.get('act'),obs.get('floor'),e['phase'])]+=1
            return {'prefix':prefix,'category':category,'phase':e['phase'],'floor':int(obs.get('floor') or 0),
                    'index':b.index,'source':b.source_label,'diagnosis':b.diagnosis}
        return None

    def snapshot(self):
        return {'candidates_generated':self.generated,'candidates_deduplicated':self.duplicates,
                'pending_branches':len(self.branches),'scheduled_by_category':dict(self.counts),
                'unique_strategic_branch_requests':len(self.submitted),'room_decision_sites':len(self.room_visits)}


class DepthFirstScheduler(StrategicScheduler):
    """Batched depth-first macro search, not exhaustive game-action DFS.

    New reachable suffixes push their alternatives above older siblings. Native
    rollouts execute to terminal/cap; a cap leaves the region UNKNOWN. Up to the
    worker count siblings can be outstanding, so this is parallel DFS ordering.
    """
    def next(self):
        while self.branches:
            b=self.branches.pop()
            prefix=b.result['trace'][:b.index]+[b.action];key=canonical(prefix)
            if key in self.submitted or key in self.executed:continue
            self.submitted.add(key)
            e=b.result['decision_evidence'][b.index];obs=e.get('observation') or {}
            self.counts['depth_first']+=1;self.source_visits[b.source_label]+=1
            self.room_visits[(obs.get('act'),obs.get('floor'),e['phase'])]+=1
            return {'prefix':prefix,'category':'depth_first','phase':e['phase'],
                    'floor':int(obs.get('floor') or 0),'index':b.index,
                    'source':b.source_label,'diagnosis':b.diagnosis}
        return None
