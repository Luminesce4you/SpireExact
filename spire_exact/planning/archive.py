"""Full-prefix checkpoint trie, exact-request cache, diverse heuristic frontier.
Feature bins are scheduling aids ONLY: never semantic equality or no-good proofs.
"""
from __future__ import annotations
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from ..canonical import canonical
from .io import read_json,write_json

@dataclass(frozen=True)
class CheckpointRef:
    path: Path
    prefix_length: int
    bytes: int
    floor: int
    act: int

class CheckpointArchive:
    def __init__(self,context: dict,identity: dict,byte_limit=512*1024**2):
        self.context=context;self.identity=identity;self.byte_limit=byte_limit
        self.root={};self.entries=OrderedDict();self.bytes=0;self.hits=0;self.lineages={}
    def _remove(self,path):
        entry=self.entries.pop(path,None)
        if entry is None:return
        ref,node=entry
        if node.get(None)==ref:node.pop(None,None)
        self.bytes-=ref.bytes
        # Prune abandoned branches too, not just checkpoint markers.
        for parent,key,child in reversed(self.lineages.pop(path,[])):
            if child:break
            if parent.get(key) is child:parent.pop(key)
    def _context_matches(self,context):
        return all(context.get(k)==self.context.get(k) for k in ('seed','character','ascension','unlocks')) and context.get('information')=='full'
    def add(self,path: Path):
        payload=read_json(path,resolve_checkpoint=False)
        if payload.get('schema')!='spire-map-checkpoint/v1':return False
        p=payload.get('payload',{})
        if not self._context_matches(p.get('context',{})):return False
        if canonical(p.get('identity'))!=canonical(self.identity):return False
        trace=p.get('history',[])
        if not isinstance(trace,list):return False
        if 'evidence_ref'in payload:
            reference=payload['evidence_ref']
            if reference.get('file')!='decision.json.gz'or reference.get('count')!=len(trace)or not(path.parent.parent/'decision.json.gz').is_file():return False
        elif len(p.get('evidence',[]))!=len(trace):return False
        observation=p.get('observation',{});size=path.stat().st_size
        if size>self.byte_limit:return False
        ref=CheckpointRef(path.resolve(),len(trace),size,int(observation.get('floor',0)),int(observation.get('act',0)))
        # Reindexing the same path must not leave an old branch reachable.
        self._remove(str(ref.path))
        node=self.root
        for action in trace:
            node=node.get(canonical(action))
            if node is None:break
        if node is not None and node.get(None):self._remove(str(node[None].path))
        node=self.root;lineage=[]
        for action in trace:
            key=canonical(action);child=node.setdefault(key,{})
            lineage.append((node,key,child));node=child
        node[None]=ref;self.entries[str(ref.path)]=(ref,node)
        self.lineages[str(ref.path)]=lineage;self.bytes+=size
        while self.bytes>self.byte_limit:self._remove(next(iter(self.entries)))
        return True
    def import_result(self,result):
        for row in result.get('checkpoints',[]):
            try:self.add(Path(row['path']))
            except (OSError,ValueError,KeyError):continue
    def nearest(self,prefix: list) -> CheckpointRef|None:
        node=self.root;best=node.get(None)
        if best is not None and not best.path.exists():best=None
        for action in prefix:
            node=node.get(canonical(action))
            if node is None:break
            ref=node.get(None)
            if ref is not None and ref.path.exists():best=ref
        if best is not None:
            self.hits+=1
            if str(best.path) in self.entries:self.entries.move_to_end(str(best.path))
        return best
    def snapshot(self,limit=None):
        from itertools import islice
        rows=self.entries.values() if limit is None else [self.entries[k]for k in islice(reversed(self.entries),limit)]
        return {'count':len(self.entries),'indexed_bytes':self.bytes,'hits':self.hits,
            'entries_truncated':limit is not None and len(self.entries)>limit,
            'entries':[{'path':str(r.path),'prefix_length':r.prefix_length,'floor':r.floor,'act':r.act} for r,_ in rows]}

class ResultCache:
    def __init__(self,byte_limit=64*1024**2):
        self.limit=byte_limit;self.bytes=0;self.rows=OrderedDict();self.hits=0
    def get(self,key: bytes):
        if key not in self.rows:return None
        value,size=self.rows.pop(key);self.rows[key]=(value,size);self.hits+=1
        return deepcopy(value)
    def put(self,key: bytes,value):
        size=len(key)+len(json.dumps(value,ensure_ascii=False).encode())
        if size>self.limit:return
        if key in self.rows:self.bytes-=self.rows.pop(key)[1]
        self.rows[key]=(deepcopy(value),size);self.bytes+=size
        while self.bytes>self.limit:self.bytes-=self.rows.popitem(last=False)[1][1]

STRATEGIC={'map','shop','rest','card_reward','event','select_cards','rewards','treasure'}

def classify_failure(result: dict) -> str:
    reason=str(result.get('reason') or '')
    if 'SEARCH_CANCELLED' in reason:return 'SEARCH_CANCELLED'
    if 'NATIVE_WORKER_CRASH' in reason:return 'NATIVE_CRASH'
    if 'INVALID_STATE' in reason:return 'INVALID_STATE'
    if 'UNSUPPORTED_MECHANIC' in reason:return 'MECHANISM_OR_HOST_GAP'
    if 'CHECKPOINT_' in reason or 'replay_trajectory' in reason:return 'RESTORE_OR_REPLAY_MISMATCH'
    if result.get('status')=='TERMINAL':
        return 'NATIVE_WIN_CANDIDATE' if result.get('value',[0])[0]==1 else 'NATIVE_ROUTE_DEATH'
    if result.get('status')=='BUDGET' or 'candidate_' in reason:return 'SEARCH_BUDGET'
    if 'TIMEOUT' in reason or 'timeout' in reason.lower():return 'TIMEOUT'
    if 'MEMORY' in reason:return 'RESOURCE_LIMIT'
    if result.get('status')=='UNSUPPORTED':return 'MECHANISM_OR_HOST_GAP'
    if result.get('status')=='DECISION':return 'DECISION_BOUNDARY'
    return 'UNKNOWN'

def combat_loss_progress(result: dict) -> dict:
    """Observed fatal-fight progress, never a victory or infeasibility bound.

    Keep the last observed HP for disappearing enemies rather than inventing a
    kill. A legacy trace without the native terminal snapshot is explicitly a
    last-observation proxy, not claimed to include unseen enemy-turn effects.
    """
    terminal=result.get('observation')or{};segment=[]
    for evidence in result.get('decision_evidence',[]):
        obs=evidence.get('observation')or{}
        same=obs.get('act')==terminal.get('act')and obs.get('floor')==terminal.get('floor')
        if not same:continue
        if obs.get('turn')is None or not isinstance(obs.get('enemies'),list):
            segment=[];continue
        if segment and int(obs['turn'])<int(segment[-1]['turn']):segment=[]
        segment.append(obs)
    final=result.get('terminal_combat')
    observed_entry=any(obs.get('enemies')for obs in segment)
    final_valid=(isinstance(final,dict)and final.get('act')==terminal.get('act')and
                 final.get('floor')==terminal.get('floor')and isinstance(final.get('enemies'),list))
    if final_valid:segment.append(final)
    initial={};last={};turns=0;completed_life_hp=0;revivals=0
    for obs in segment:
        turns=max(turns,int(obs.get('turn')or 0))
        for enemy in obs['enemies']:
            if enemy.get('combat_id')is None:continue
            key=(enemy.get('id'),enemy['combat_id']);hp=max(0,float(enemy.get('hp')or 0))
            if key not in initial:
                initial[key]=hp
            elif last[key]==0 and hp>0:
                # Count only a directly observed return from zero after a
                # witnessed positive life. Do not invent unseen kills/phases.
                if initial[key]>0:
                    completed_life_hp+=initial[key];revivals+=1
                initial[key]=hp
            last[key]=hp
    current_total=sum(initial.values())
    current_removed=sum(max(0,initial[key]-last[key])for key in initial)
    total=completed_life_hp+current_total
    removed=completed_life_hp+current_removed
    available=total>0 and observed_entry
    return {'available':available,'hp_removed_fraction':removed/total if available else None,
            'current_life_hp_removed_fraction':current_removed/current_total if available and current_total>0 else None,
            'revivals_observed':revivals,'observed_life_hp':total,'observed_hp_removed':removed,
            'metric_scope':'observed lives only; unknown future forms excluded',
            'turns_observed':turns,'observed_enemy_count':len(initial),
            'source':'native_terminal_and_trace'if final_valid else'last_observed_trace',
            'terminal_snapshot_available':final_valid}

def combat_progress_key(progress):
    """Heuristic ordering, never a win, bound, or proof of irreversible progress."""
    fraction=progress.get('current_life_hp_removed_fraction',progress.get('hp_removed_fraction'))
    return (progress.get('revivals_observed',0),fraction if progress.get('available')and fraction is not None else -1,
            progress.get('turns_observed',0))

def utility(result: dict) -> tuple:
    """Candidate ranking, NEVER a bound. Role-free dynamic resource evaluation."""
    status=classify_failure(result)
    obs=result.get('observation') or {}
    hp=float(obs.get('hp') or 0);maxhp=max(1,float(obs.get('max_hp') or 1))
    win=status=='NATIVE_WIN_CANDIDATE'
    viable=status in ('SEARCH_BUDGET','DECISION_BOUNDARY','NATIVE_WIN_CANDIDATE') and hp>0
    if status=='NATIVE_ROUTE_DEATH':
        progress=combat_loss_progress(result)
        return (0,0,int(obs.get('act',0)),int(obs.get('floor',0)),*combat_progress_key(progress))
    return (int(win),int(viable),int(obs.get('act',0)),int(obs.get('floor',0)),0,
        hp/maxhp+.12*sum(x is not None for x in obs.get('potions',[]))+.0006*float(obs.get('gold',0)),0)

def descriptor(result: dict):
    from .strategy import capability_signature
    obs=result.get('observation') or {}
    hp=float(obs.get('hp') or 0);mx=max(1,float(obs.get('max_hp') or 1))
    return (obs.get('act'),obs.get('floor'),int(4*hp/mx),
        sum(x is not None for x in obs.get('potions',[])),len(obs.get('deck',[]))//5,
        len(obs.get('relics',[]))//3) + capability_signature(obs)

class DiverseFrontier:
    def __init__(self,per_bin=2,limit=48):self.per_bin=per_bin;self.limit=limit;self.rows=[]
    def add(self,result: dict,label: str):
        if not result.get('trace'):return
        key=canonical(result['trace'])
        if any(x['key']==key for x in self.rows):return
        # The frontier is a reporting/diversity index, not the trajectory store.
        # Holding the full result here retained hundreds of entire transcripts.
        row={'key':key,'bin':descriptor(result),'quality':utility(result),'actions':len(result['trace']),'label':label}
        same=sorted([x for x in self.rows if x['bin']==row['bin']]+[row],key=lambda x:x['quality'],reverse=True)
        kept={id(x) for x in same[:self.per_bin]}
        self.rows=[x for x in self.rows if x['bin']!=row['bin'] or id(x) in kept]
        if id(row) in kept:self.rows.append(row)
        # Heuristic archive eviction, all dropped regions remain unresolved.
        self.rows=sorted(self.rows,key=lambda x:x['quality'],reverse=True)[:self.limit]
    def snapshot(self):return [{'label':r['label'],'descriptor':r['bin'],'heuristic_quality':r['quality'],
        'actions':r['actions']} for r in self.rows]

def decision_groups(result: dict,window: int,seen: set,limit=8):
    trace=result.get('trace',[]);evidence=result.get('decision_evidence',[])
    indices=[i for i,e in enumerate(evidence[:len(trace)]) if e.get('phase') in STRATEGIC
             and len(e.get('available_actions',[]))>1]
    groups=[]
    for i in reversed(indices[-window:]):
        e=evidence[i];key=canonical(trace[:i])
        if key in seen:continue
        seen.add(key)
        actions=e['available_actions']
        # Keep baseline plus alternatives, deterministic order; caps are explicit heuristic deferrals.
        chosen=trace[i]
        ordered=[chosen]+[a for a in actions if canonical(a)!=canonical(chosen)]
        groups.append({'index':i,'phase':e['phase'],'floor':int((e.get('observation') or {}).get('floor',0)),
                       'prefixes':[trace[:i]+[a] for a in ordered[:limit]],
                       'deferred_actions':max(0,len(ordered)-limit)})
    return groups

def failure_combat_prefix(result: dict):
    if classify_failure(result)!='NATIVE_ROUTE_DEATH':return None
    floor=(result.get('observation') or {}).get('floor')
    for i,e in enumerate(result.get('decision_evidence',[])):
        if e.get('phase')=='combat' and (e.get('observation') or {}).get('floor')==floor:
            return {'prefix':result['trace'][:i],'floor':floor,'entry_observation':e['observation']}
    return None
