"""Read-only gate/fit clocks. No synthetic labels or native-win inference."""
from collections import Counter
from ..canonical import canonical
from .archive import classify_failure
from .gatemodel import boss_fight_rows
from .tail_search import nonnegative


class GateTiming:
    def __init__(self, enabled=False, *, prefixes=None):
        self.enabled=enabled
        self.prefixes=prefixes
        self.entries={};self.passes={};self.first_entry={};self.first_pass={}
        self.first_fit={};self.joint_fit=None;self.final_act=None

    def observe(self, result, seconds, *, cache_hit=False):
        if not self.enabled or result.get('synthetic') or cache_hit or not nonnegative(seconds):return
        if classify_failure(result) not in ('NATIVE_ROUTE_DEATH','NATIVE_WIN_CANDIDATE','SEARCH_BUDGET','DECISION_BOUNDARY'):return
        campaign=result.get('campaign') or {}
        acts=campaign.get('act_count')
        if type(acts) is int and acts>0 and campaign.get('final_act_boss_count')==2:self.final_act=acts-1
        trace=result.get('trace') or []
        path=self.prefixes.index(trace) if self.prefixes is not None else None
        key_at=lambda i:path[i] if path is not None else canonical(trace[:i])
        # Observe known entries even when a request stops inside F2. Unknown
        # combat outcomes cannot be converted to a pass or a failed sample.
        ordinal=Counter();seen=set()
        for i,e in enumerate((result.get('decision_evidence') or [])[:len(trace)]):
            obs=e.get('observation') or {}
            combat=e.get('phase')=='combat' or (e.get('phase')=='select_cards' and obs.get('turn') is not None)
            act,floor=obs.get('act'),obs.get('floor')
            if not combat or obs.get('room')!='Boss' or type(act) is not int or type(floor) is not int:continue
            if (act,floor) in seen:continue
            seen.add((act,floor));gate=str((act,ordinal[act]));ordinal[act]+=1
            self.entries.setdefault(gate,set()).add(key_at(i));self.first_entry.setdefault(gate,seconds)
        for row in boss_fight_rows(result):
            gate=str(tuple(row['gate']));key=key_at(row['index'])
            self.entries.setdefault(gate,set()).add(key);self.first_entry.setdefault(gate,seconds)
            if row['lost'] is None:
                self.passes.setdefault(gate,set()).add(key);self.first_pass.setdefault(gate,seconds)

    def fitted(self, models, joint, seconds):
        if not self.enabled or not nonnegative(seconds):return
        for gate,model in (getattr(models,'gates',{}) or {}).items():
            if getattr(model,'fitted',0):
                self.first_fit.setdefault(str(tuple(gate)),{'seconds':seconds,'rows':len(model.entries)})
        if getattr(joint,'fitted',0) and self.joint_fit is None:
            self.joint_fit={'seconds':seconds,'rows':len(joint.entries)}

    def snapshot(self):
        return {'enabled':self.enabled,'final_act':self.final_act,
            'first_entry':self.first_entry,'first_pass':self.first_pass,'first_fit':self.first_fit,
            'joint_first_fit':self.joint_fit,
            'distinct_entries':{k:len(v) for k,v in self.entries.items()},
            'distinct_passes':{k:len(v) for k,v in self.passes.items()},
            'scope':'known real outcomes at coordinator availability; not internal gate crossing timestamps'}
