"""Descriptive right-censored search-time summaries, not fitted tail laws.

For complete capped trials, empirical restricted moments are identifiable.
KM summaries additionally assume independent censoring; adaptive search stops
may violate that. Never extrapolate quantiles beyond observed survival support.
"""
from __future__ import annotations
from collections import defaultdict
import math
import statistics


def numeric(value):
    return type(value) in (float,int) and math.isfinite(value) and value>=0


def survival_summary(samples, horizon):
    """Rows {seconds,event}; event=True means observed success, not a death.

    Zero-time events supported; events precede censoring at an equal timestamp.
    RMST and tail area become unknown after last observation if survival>0.
    """
    if not numeric(horizon) or horizon<=0:raise ValueError('positive horizon required')
    groups=defaultdict(lambda:[0,0])
    for row in samples:
        t,event=row['seconds'],row['event']
        if not numeric(t) or type(event) is not bool:raise ValueError('invalid censored sample')
        groups[min(t,horizon)][0 if event and t<=horizon else 1]+=1
    n=len(samples);risk=n;survival=1.0;last=0.0;area=0.0;tail_area=0.0;curve=[]
    quantiles={str(q):None for q in (.5,.9,.95)}
    for t,(events,censors) in sorted(groups.items()):
        area+=(t-last)*survival
        tail_area+=max(0,t-max(last,horizon/2))*survival
        survival*=1-events/risk
        curve.append({'seconds':t,'at_risk':risk,'events':events,'censored':censors,'survival':survival})
        for q in quantiles:
            if quantiles[q] is None and survival<=1-float(q)+1e-12:quantiles[q]=t
        risk-=events+censors;last=t
    supported=bool(n) and (last>=horizon or survival==0)
    if supported:
        area+=(horizon-last)*survival
        tail_area+=max(0,horizon-max(last,horizon/2))*survival
    return {'n':n,'horizon':horizon,'events':sum(g[0] for g in groups.values()),
        'censored':sum(g[1] for g in groups.values()),'quantiles_seconds':quantiles,
        'rmst_seconds':area if supported else None,
        'second_half_survival_area_seconds':tail_area if supported else None,
        'survival_at_horizon':survival if supported else None,'support_complete':supported,'curve':curve,
        'assumption':'KM descriptive; dependent/adaptive censoring may invalidate population interpretation'}


def restart_expectation(distribution, cutoff, overhead=0):
    """Exact finite-distribution IID restart calculation, NOT a fitted solver.

    Distribution rows are (time,probability). Charge overhead on failed restarts
    only. Fresh-start cost common to all strategies is omitted.
    """
    if not numeric(cutoff) or cutoff<=0 or not numeric(overhead):raise ValueError('invalid cutoff or overhead')
    if not distribution or any(not numeric(t) or not numeric(p) for t,p in distribution):raise ValueError('invalid distribution')
    if not math.isclose(sum(p for _,p in distribution),1,abs_tol=1e-9):raise ValueError('probabilities must sum to one')
    f=sum(p for t,p in distribution if t<=cutoff)
    return (sum(min(t,cutoff)*p for t,p in distribution)+overhead*(1-f))/f if f else None


def scaling_report(rows,arms):
    report={}
    for arm in arms:
        group=[r for r in rows if r['arm']==arm]
        usable=[];omitted=[];conversion=[];fit_diagnostics=[]
        for r in group:
            reasons=[]
            for flag in ('available','context_matches_plan','settings_match_plan','resource_valid'):
                if not r.get(flag):reasons.append(flag)
            if r.get('run_state')!='completed':reasons.append('not_completed')
            if len(r.get('native_identities',[]))!=1:reasons.append('identity')
            if r.get('status')=='VERIFIED_WIN_IN_NATIVE_HOST' and not r.get('verified_evidence'):reasons.append('proof')
            cap=r['wall_cap_seconds'];success=r.get('within_coordinator_budget_win') is True
            t=r.get('time_to_first_verified_win_seconds')
            if success and (not numeric(t) or t>cap):reasons.append('time')
            # Resource/crash/early termination is NOT ordinary administrative
            # censoring at the requested cap. Keep it out of efficacy KM curves.
            if not success and r.get('stop_reason')!='time_budget':reasons.append('not_time_censored')
            if reasons:
                omitted.append({'id':r['id'],'reasons':reasons});continue
            usable.append((r,{'seconds':t if success else cap,'event':success}))
            clock=(r.get('search_metrics') or {}).get('gate_timing') or {}
            final=clock.get('final_act')
            if type(final) is int:
                f1=(clock.get('first_pass') or {}).get(str((final,0)))
                f2=(clock.get('first_pass') or {}).get(str((final,1)))
                fits=clock.get('first_fit') or {}
                realfit=(fits.get(str((final,1))) or {}).get('seconds')
                jointfit=(clock.get('joint_first_fit') or {}).get('seconds')
                fit_diagnostics.append({'id':r['id'],'seed':r.get('seed'),'solver_seed':r.get('solver_seed'),
                    'first_F1_pass':f1,'first_F2_pass':f2,
                    'real_F2_first_fit':realfit,'joint_first_fit':jointfit,
                    'real_fit_minus_F1_seconds':realfit-f1 if numeric(realfit) and numeric(f1) else None,
                    'joint_fit_minus_F1_seconds':jointfit-f1 if numeric(jointfit) and numeric(f1) else None,
                    'F2_right_censored':numeric(f1) and f2 is None,
                    'scope':'fit clock availability, not proof that the model changed a dispatch'})
                if numeric(f1) and f1<=cap:
                    if numeric(f2) and f1<=f2<=cap:
                        conversion.append({'seconds':f2-f1,'event':True})
                    elif f2 is None:
                        conversion.append({'seconds':cap-f1,'event':False})
        caps=sorted({r['wall_cap_seconds'] for r in group})
        curves={}
        for cap in caps:
            samples=[s for r,s in usable if r['wall_cap_seconds']==cap]
            curves[str(cap)]=survival_summary(samples,cap)
        per_seed={}
        for seed in sorted({str(r.get('seed')) for r,_ in usable if r.get('seed') is not None}):
            per_seed[seed]={str(cap):survival_summary([s for r,s in usable
                if str(r.get('seed'))==seed and r['wall_cap_seconds']==cap],cap) for cap in caps}
        prefix=[]
        for budget in (300,900,1800,3600,7200,10800):
            eligible=[(r,s) for r,s in usable if r['wall_cap_seconds']>=budget]
            declared=sum(r['wall_cap_seconds']>=budget for r in group)
            wins=sum(s['event'] and s['seconds']<=budget for _,s in eligible)
            prefix.append({'seconds':budget,'declared':declared,'auditable':len(eligible),'observed_wins':wins,
                'observed_success_fraction_lower_bound':wins/declared if declared else None})
        conversion_horizon=max((s['seconds'] for s in conversion),default=0)
        report[arm]={'declared_runs':len(group),'auditable_runs':len(usable),'omitted':omitted,
            'by_independent_cap':curves,'long_run_prefixes':prefix,
            'per_game_seed':per_seed,'fit_latency_diagnostics':fit_diagnostics,
            'tail_identification':'pooled seed-difficulty mixtures are not evidence of IID within-seed heavy tails; no parametric tail law fitted',
            'F1_to_F2_observable_gap':survival_summary(conversion,conversion_horizon) if conversion_horizon>0 else
                {'n':len(conversion),'zero_gap_events':sum(s['event'] for s in conversion)},
            'caveat':'global first F1 pass to global first F2 pass, possibly different lineages; not paired combat durations; prefix curves are not fresh short runs'}
    return report
