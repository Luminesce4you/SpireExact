"""Memory-compact implementation of the existing weighted macro order.

Prefix IDs name exact parent + full canonical action bytes in a trie. They do
not equate projected states or hash digests. Heap priorities only increase as
visits increase: stale keys are lower bounds, refreshed before selection.
"""
from collections import Counter, deque
from dataclasses import dataclass
import heapq,json
from ..canonical import canonical,ContractError
from .strategy import StrategicScheduler,FailureAnalyzer,strategic_phase

class PrefixTrie:
    def __init__(self):self.edges={};self.parents=[-1];self.actions=[b''];self.byte_count=0
    def child(self,parent,action):
        data=canonical(action);key=(parent,data)
        known=self.edges.get(key)
        if known is not None:return known
        node=len(self.parents);self.edges[key]=node;self.parents.append(parent);self.actions.append(data);self.byte_count+=len(data)
        return node
    def index(self,history):
        nodes=[0]
        for action in history:nodes.append(self.child(nodes[-1],action))
        return nodes
    def restore(self,node):
        actions=[]
        while node:
            actions.append(json.loads(self.actions[node]));node=self.parents[node]
        actions.reverse();return actions

@dataclass(slots=True)
class CompactBranch:
    key:int
    index:int
    category:str
    diagnosis:dict
    source_label:str
    order:int
    floor:int
    act:int
    phase:str

class IndexedStrategicScheduler:
    weights=StrategicScheduler.weights
    def __init__(self,scope_prefix=None,site_cap=None,*,recover_deferred=False):
        self.scope_length=len(scope_prefix or [])
        self.scope_bytes=canonical(scope_prefix or [])
        self.prefixes=PrefixTrie();self.seen=set();self.executed=set();self.submitted=set()
        self.counts=Counter();self.room_visits=Counter();self.source_visits=Counter()
        self.branches={};self.heaps={k:[]for k in self.weights};self.order=0;self.generated=0;self.duplicates=0
        # Optional heuristic deferral for very wide menus (e.g. 100+ card
        # selections): only an evenly spaced subset is queued here. None keeps
        # the exact historical order; deferred options stay unresolved.
        if site_cap is not None and(type(site_cap)is not int or site_cap<1):raise ValueError('Positive site cap required')
        self.site_cap=site_cap;self.deferred=0;self.last_key=None
        if type(recover_deferred) is not bool:raise ValueError('recover_deferred must be boolean')
        self.recover_deferred=recover_deferred
        self.deferred_branches={};self.deferred_order=deque();self.promoted=0
    def take(self,key):
        """Record an alternative submitted by another allocator of the same trie."""
        self.branches.pop(key,None);self.deferred_branches.pop(key,None)
        self.seen.add(key);self.submitted.add(key)
    def pending(self,key):return key not in self.executed and key not in self.submitted
    def _priority(self,b,category):
        death_floor=b.diagnosis['floor'];death_act=b.diagnosis['act']
        target=max(1,death_floor-(10 if b.diagnosis.get('room')=='Boss'else 5))
        if category=='exploration':target=1
        elif category=='preparation':target=max(1,death_floor-3)
        elif category=='failure'and'hp_resource_mismanagement'in b.diagnosis['hypotheses']:target=max(1,death_floor-4)
        fit=0
        if category=='failure':
            if any(h in b.diagnosis['hypotheses']for h in('insufficient_scaling','insufficient_block','deck_bloat')):fit=int(b.category=='deck')
            if'unspent_shop_resources'in b.diagnosis['hypotheses']and b.phase=='shop':fit+=2
        return(self.room_visits[(b.act,b.floor,b.phase)],-fit,abs(b.floor-target),self.source_visits[b.source_label],abs(b.act-death_act),b.order)
    def add(self,result,label):
        trace=result.get('trace',[]);evidence=result.get('decision_evidence',[])[:len(trace)]
        if trace and canonical(trace[:self.scope_length])!=self.scope_bytes:
            raise ContractError('Candidate escaped the immutable diagnostic scope prefix')
        path=self.prefixes.index(trace);diagnosis=FailureAnalyzer.analyze(result)
        for i,e in enumerate(evidence):
            if strategic_phase(e):
                key=path[i+1];self.seen.add(key);self.executed.add(key);self.branches.pop(key,None)
                self.deferred_branches.pop(key,None)
        room_phases=set()
        for i,e in enumerate(evidence):
            if i<self.scope_length:continue
            category=strategic_phase(e)
            if not category:continue
            obs=e.get('observation')or{};floor=int(obs.get('floor')or 0);act=int(obs.get('act')or 0)
            room=(obs.get('act'),floor,e.get('phase'),(obs.get('selection')or{}).get('purpose'))
            if room in room_phases and e.get('phase')!='card_reward':continue
            room_phases.add(room)
            actions=[a for a in e.get('available_actions',[])if a.get('kind')not in('discard_potion','reward','rewards_skip')
                     and canonical(a)!=self.prefixes.actions[path[i+1]]]
            delayed=set()
            if self.site_cap is not None and len(actions)>self.site_cap:
                step=len(actions)/self.site_cap;self.deferred+=len(actions)-self.site_cap
                selected={int(k*step) for k in range(self.site_cap)}
                if self.recover_deferred:
                    delayed=set(range(len(actions)))-selected
                else:
                    actions=[actions[int(k*step)]for k in range(self.site_cap)]
            for position,action in enumerate(actions):
                key=self.prefixes.child(path[i],action);self.generated+=1
                if key in self.seen:self.duplicates+=1;continue
                self.seen.add(key);self.order+=1
                b=CompactBranch(key,i,'preparation'if action.get('kind')=='use_potion'else category,diagnosis,label,self.order,floor,act,e['phase'])
                if position in delayed:
                    self.deferred_branches[key]=b;self.deferred_order.append(key)
                else:self._activate(b)
    def _activate(self,b):
        self.branches[b.key]=b
        for cat,heap in self.heaps.items():
            if cat in('failure','exploration')or b.category==cat or cat=='preparation'and b.category=='resources':
                heapq.heappush(heap,(self._priority(b,cat),b.key))
    def promote_deferred(self):
        """One FIFO promotion per explorer opportunity. Full prefix identity.

        Counts/beam deferral are not exclusions. Finite menus are eventually
        activated given enough explorer opportunities; no finite-budget solve
        or coverage guarantee is made. Focus may take a deferred item first.
        """
        while self.deferred_order:
            key=self.deferred_order.popleft();b=self.deferred_branches.pop(key,None)
            if b is not None and self.pending(key):
                self._activate(b);self.promoted+=1;return key
        return None
    def next(self):
        if self.recover_deferred:self.promote_deferred()
        if not self.branches:return None
        total=sum(self.counts.values())+1
        for category in sorted(self.weights,key=lambda c:self.weights[c]*total-self.counts[c],reverse=True):
            heap=self.heaps[category]
            while heap:
                old,key=heapq.heappop(heap);b=self.branches.get(key)
                if b is None:continue
                current=self._priority(b,category)
                if current!=old:
                    if current<old:raise AssertionError('Priority decreased; lazy heap proof no longer valid')
                    heapq.heappush(heap,(current,key));continue
                self.branches.pop(key);self.submitted.add(key);self.counts[category]+=1;self.last_key=key
                self.source_visits[b.source_label]+=1;self.room_visits[(b.act,b.floor,b.phase)]+=1
                return {'prefix':self.prefixes.restore(key),'category':category,'phase':b.phase,'floor':b.floor,
                        'index':b.index,'source':b.source_label,'diagnosis':b.diagnosis}
        return None
    def snapshot(self):
        return {'candidates_generated':self.generated,'candidates_deduplicated':self.duplicates,
                'pending_branches':len(self.branches),'scheduled_by_category':dict(self.counts),
                'unique_strategic_branch_requests':len(self.submitted),'room_decision_sites':len(self.room_visits),
                'shared_prefix_nodes':len(self.prefixes.parents),'shared_action_bytes':self.prefixes.byte_count,
                'prefix_identity':'exact parent plus full action bytes; not state equivalence',
            **({'recover_deferred':True,'deferred_pending':len(self.deferred_branches),
                'deferred_promoted':self.promoted} if self.recover_deferred else {}),
                **({'site_cap':self.site_cap,'options_deferred_by_site_cap':self.deferred}if self.site_cap is not None else{})}
