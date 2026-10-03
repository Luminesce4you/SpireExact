"""Experimental rollout-result guidance; no handcrafted card value table.

It ranks completed native trajectories for *allocation* only. A death, dropped
trajectory or limited search still cannot prove that any route is infeasible.
"""
from spire_exact.planning.strategy import StrategicScheduler,FailureAnalyzer
from spire_exact.planning.archive import classify_failure
from spire_exact.canonical import canonical


def native_progress(result):
    """Observed room reach and same-enemy HP reduction, never a victory bound.

    Terminal observation may have cleared combat entities. The last recorded
    decision is then only a pre-action proxy, explicitly not the death state.
    Do not invent a denominator for summons/replacements/resurrections.
    """
    classification=classify_failure(result)
    if classification not in ('NATIVE_ROUTE_DEATH','NATIVE_WIN_CANDIDATE'):
        return None
    obs=result.get('observation')or{};floor=int(obs.get('floor')or 0)
    combat=[e.get('observation')or{} for e in result.get('decision_evidence',[])
            if e.get('phase')=='combat' and (e.get('observation')or{}).get('floor')==floor]
    damage_fraction=0.
    if combat:
        def enemies(o):return {(e.get('id'),e.get('combat_id')):float(e.get('hp')or 0)
                                for e in o.get('enemies')or[]}
        initial,last=enemies(combat[0]),enemies(combat[-1])
        if initial and initial.keys()==last.keys() and sum(initial.values())>0:
            damage_fraction=max(0.,min(1.,1-sum(last.values())/sum(initial.values())))
    return (int(classification=='NATIVE_WIN_CANDIDATE'),int(obs.get('act')or 0),floor,damage_fraction)


class IncumbentScheduler(StrategicScheduler):
    """Keep the existing macro quotas; exploit the top4 complete rollouts.

    Exploration quota still ranges over all pending branches. Other categories
    prefer the current elite source histories, then fall back to the old queue.
    Every alternative remains pending with its full legal action prefix.
    """
    def __init__(self):
        super().__init__();self.qualities={};self.histories={};self.elite=set();self.promotions=0;self.credited=0

    def add(self,result,label):
        history=tuple(canonical(a) for a in result.get('trace',[]))
        self.histories[label]=history
        quality=native_progress(result)
        if quality is not None:
            self.qualities[label]=quality
            elite=set(sorted(self.qualities,key=lambda x:self.qualities[x],reverse=True)[:4])
            self.promotions+=len(elite-self.elite);self.elite=elite
        super().add(result,label)
        if quality is not None:
            trace=result.get('trace',[]);evidence=result.get('decision_evidence',[])
            diagnosis=FailureAnalyzer.analyze(result)
            shared={}
            # Backpropagate a better continuation to siblings at its exact
            # ancestor histories. Otherwise first-source dedup freezes their
            # credit to an early losing rollout forever.
            for branch in self.branches:
                if branch.index>=min(len(trace),len(evidence)):continue
                previous=self.qualities.get(branch.source_label)
                if previous is not None and previous>=quality:continue
                if branch.source_label not in shared:
                    old=self.histories[branch.source_label]
                    shared[branch.source_label]=next((i for i,(a,b) in enumerate(zip(history,old)) if a!=b),min(len(history),len(old)))
                if shared[branch.source_label]<branch.index:continue
                action=canonical(branch.action)
                if not any(canonical(a)==action for a in evidence[branch.index].get('available_actions',[])):continue
                branch.result=result;branch.source_label=label;branch.diagnosis=diagnosis
                self.credited+=1

    def _priority(self,branch,category):
        original=super()._priority(branch,category)
        if category=='exploration':return (0,*original)
        return (0 if branch.source_label in self.elite else 1,*original)

    def snapshot(self):
        return {**super().snapshot(),'completed_native_sources':len(self.qualities),
                'elite_promotions':self.promotions,
                'exact_ancestor_credit_updates':self.credited,
                'elite_sources':[{'label':label,'heuristic_native_progress':self.qualities[label]}
                                 for label in sorted(self.elite)],
                'source_quality_is_proof':False}
