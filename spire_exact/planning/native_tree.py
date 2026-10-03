"""Open-loop strategic tree grounded in executed native action histories.

No card/relic/event rules live here. Native menus define action symbols. Full
canonical histories identify nodes; observed deltas are explanations, NEVER
state-equivalence keys. Terminal/capped/unsupported outcomes stay distinct.
"""
from collections import Counter
from dataclasses import dataclass,field
from decimal import Decimal,InvalidOperation
import math
from ..canonical import canonical,ContractError
from .archive import classify_failure
from .strategy import strategic_phase


def observed_delta(before,after):
    """Diagnostic consequences of a real native suffix, not inferred rules."""
    changes={}
    for name in ('hp','max_hp','gold'):
        try:changes[name]=str(Decimal(str(after[name]))-Decimal(str(before[name])))
        except (KeyError,InvalidOperation):pass
    for name in ('deck','relics','potions'):
        left=Counter(canonical(x) for x in before.get(name,[]) if x is not None)
        right=Counter(canonical(x) for x in after.get(name,[]) if x is not None)
        changes[name]={'added':[k.decode('utf-8') for k in (right-left).elements()],
                       'removed':[k.decode('utf-8') for k in (left-right).elements()]}
    return changes


@dataclass
class Edge:
    action:dict
    trials:int=0
    complete:int=0
    native_win_candidates:int=0
    unknown:int=0
    progress_total:int=0
    launches:int=0
    children:set=field(default_factory=set)
    effects:list=field(default_factory=list)


@dataclass
class Node:
    prefix:list
    phase:str
    observation:dict
    actions:dict
    visits:int=0


class NativeTreeScheduler:
    def __init__(self,scope=None):
        self.scope=scope or {}
        self.nodes={};self.roots=set();self.farthest_floor=1
        self.generated=0;self.duplicates=0;self.proposals=0
        self.errors=[];self.categories=Counter()

    def add(self,result,label):
        trace=result.get('trace',[]);evidence=result.get('decision_evidence',[])
        classification=classify_failure(result)
        terminal=classification in ('NATIVE_ROUTE_DEATH','NATIVE_WIN_CANDIDATE')
        reached=int((result.get('observation') or {}).get('floor') or 0)
        self.farthest_floor=max(self.farthest_floor,reached)
        path=[]
        for i,e in enumerate(evidence[:len(trace)]):
            if not strategic_phase(e):continue
            menu=e.get('available_actions',[])
            # These exclusions define a heuristic macro policy, not an exact
            # legal-action oracle. Primitive native menus remain in the evidence.
            menu=[a for a in menu if a.get('kind') not in ('discard_potion','reward','rewards_skip')]
            if len(menu)<2:continue
            key=canonical({'scope':self.scope,'history':trace[:i]});chosen=canonical(trace[i]);self.generated+=len(menu)
            actions={canonical(a):Edge(a) for a in menu}
            if chosen not in actions:continue
            if key not in self.nodes:
                self.nodes[key]=Node(trace[:i],e['phase'],e.get('observation') or {},actions)
            else:
                self.duplicates+=len(menu)
                if set(self.nodes[key].actions)!=set(actions):
                    # A fixed history must not silently acquire another menu.
                    self.errors.append({'label':label,'index':i,'reason':'NATIVE_MENU_DIVERGENCE'})
                    continue
                if canonical(self.nodes[key].observation)!=canonical(e.get('observation') or {}):
                    self.errors.append({'label':label,'index':i,'reason':'NATIVE_OBSERVATION_DIVERGENCE'})
                    continue
            node=self.nodes[key];edge=node.actions[chosen]
            node.visits+=1;edge.trials+=1;edge.complete+=int(terminal)
            edge.native_win_candidates+=int(classification=='NATIVE_WIN_CANDIDATE')
            edge.unknown+=int(not terminal)
            if terminal:edge.progress_total+=reached
            path.append((key,chosen,i))
        if path:self.roots.add(path[0][0])
        for position,(key,chosen,i) in enumerate(path):
            node=self.nodes[key];edge=node.actions[chosen]
            if position+1<len(path):
                child_key,_,j=path[position+1];edge.children.add(child_key)
                after=evidence[j].get('observation') or {}
            else:after=result.get('observation') or {}
            if len(edge.effects)<4:
                edge.effects.append({'source':label,'executed_prefix_length':i,
                                     'delta':observed_delta(node.observation,after),
                                     'classification':classification})

    def _select_edge(self,node):
        edges=list(node.actions.items())
        # Progressive widening prevents a multi-select menu from consuming the
        # entire budget. All omitted actions remain unresolved and available.
        width=min(len(edges),max(1,math.ceil(math.sqrt(node.visits+1))))
        tried=[x for x in edges if x[1].trials or x[1].launches]
        untried=[x for x in edges if not x[1].trials and not x[1].launches]
        if len(tried)<width and untried:return untried[0]
        if not tried:return edges[0]
        def score(item):
            _,edge=item
            n=max(edge.trials,edge.launches)
            # The native terminal result is primary. Native progress is an
            # explicit search proxy while full wins are sparse, never a bound.
            value=edge.native_win_candidates/max(1,edge.complete)
            progress=edge.progress_total/max(1,edge.complete)/self.farthest_floor
            exploration=math.sqrt(2*math.log(node.visits+self.proposals+2)/(n+1))
            return value+progress+exploration
        return max(tried,key=score)

    def next(self):
        if not self.roots:return None
        # Roots contain exact histories. No projected state signatures merge them.
        key=min(self.roots,key=lambda k:(len(self.nodes[k].prefix),self.nodes[k].visits))
        visited=set()
        while key not in visited:
            visited.add(key);node=self.nodes[key];action_key,edge=self._select_edge(node)
            unseen=not edge.trials and not edge.launches
            children=[k for k in edge.children if k in self.nodes]
            if unseen or not children:
                edge.launches+=1;self.proposals+=1;self.categories[node.phase]+=1
                return {'prefix':node.prefix+[edge.action],'category':'tree','phase':node.phase,
                        'floor':int(node.observation.get('floor') or 0),'index':len(node.prefix),
                        'source':'native_tree','diagnosis':{'basis':'executed native transitions'},
                        'policy':self.proposals}
            key=min(children,key=lambda k:self.nodes[k].visits)
        return None

    def snapshot(self):
        return {'candidates_generated':self.generated,'candidates_deduplicated':self.duplicates,
                'tree_nodes':len(self.nodes),'tree_edges':sum(len(n.actions) for n in self.nodes.values()),
                'scheduled_by_category':dict(self.categories),'unique_strategic_branch_requests':self.proposals,
                'menu_divergences':self.errors[-16:],'state_identity':'full canonical native action history',
                'value_is_heuristic':True,'native_win_counts_are_not_replay_certificates':True}
