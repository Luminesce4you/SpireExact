"""Paired native continuation audit; retained prefixes are diagnostic inputs only."""
from pathlib import Path
import argparse,collections,json,os,random,sys,time
from concurrent.futures import as_completed
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.planning.io import read_json,write_json
from spire_exact.canonical import canonical
from spire_exact.planning.pool import NativePool
from spire_exact.planning.resources import ResourcePlan
from tools.rolling_storage import owner_root


def prepare(out):
    owner=owner_root(ROOT)
    audit=read_json(owner/'experiments/iteration-024/completed-baseline-audit/audit.json')
    run=Path(audit['source']);manifest=read_json(run/'validation-manifest.json')
    source=run/('seed-'+manifest['seed'])
    records=read_json(source/'result.json')['evaluations']
    affected=[r['label']for r in records if any(d['encounter']=='SPINY_TOAD_NORMAL'
              for d in(r.get('advisor_metrics')or{}).get('continuation_differences',[]))]
    candidates=[r['label']for r in records if r.get('actions')and r['kind']!='combat_probe']
    rng=random.Random(301001);rng.shuffle(candidates)
    seen=set();groups=collections.defaultdict(list)
    labels=list(dict.fromkeys(affected+candidates[:150]))
    for label in labels:
        result=read_json(source/label/'data/decision.json');trace=result['trace']
        for i,item in enumerate(result['decision_evidence']):
            obs=item.get('observation')or{}
            if obs.get('turn')!=1 or not obs.get('enemies')or obs.get('room')not in('Monster','Elite','Boss'):continue
            previous=(result['decision_evidence'][i-1].get('observation')or{})if i else{}
            if previous.get('floor')==obs['floor']and previous.get('enemies'):continue
            prefix=trace[:i];key=canonical(prefix)
            if key in seen:continue
            seen.add(key)
            target=label in affected and any(e['id']=='SPINY_TOAD'for e in obs['enemies'])
            category='affected'if target else obs['room']
            groups[category].append({'source_label':label,'prefix':prefix,'observation':obs,'category':category})
    # Known failure controls are intentionally oversampled; this is not an
    # unbiased estimate of future encounter or seed failure prevalence.
    quotas={'affected':min(16,len(groups['affected'])),'Elite':30,'Boss':30}
    quotas['Monster']=100-sum(quotas.values())
    chosen=[]
    for category,count in quotas.items():
        pool=groups[category];rng.shuffle(pool)
        if len(pool)<count:raise ValueError(f'Insufficient {category} cases: {len(pool)} < {count}')
        chosen.extend(pool[:count])
    # First four alternate an affected entry and a normal control for smoke.
    targets=[c for c in chosen if c['category']=='affected']
    controls=[c for c in chosen if c['category']=='Monster']
    head=[targets[0],controls[0],targets[1],controls[1]]
    chosen=head+[c for c in chosen if not any(c is x for x in head)]
    rows=[]
    for i,case in enumerate(chosen):
        original=read_json(source/case['source_label']/'request.json')
        rows.append({**case,'id':f'fight-{i:03}',
                     'context':{k:original[k]for k in('seed','character','ascension','unlocks')},
                     'advisor':original['advisor'],'policy_seed':original['policy_seed']})
    write_json(out,{'source':str(source),'selection_seed':301001,'cases':rows,'quotas':quotas,
                   'panel':'DEV','eligible_for_training':False,
                   'counts_as_fresh_win':False,'scope':'100 distinct exact prefixes; one correlated DEV seed; targeted failure oversampling'})
    print(json.dumps({'event':'audit_cases_prepared','cases':len(rows),'quotas':quotas}),flush=True)


def run(cases_file,out,limit):
    selection=read_json(cases_file);cases=selection['cases'][:limit]
    if out.exists()and any(out.iterdir()):raise ValueError('Audit output must be empty')
    out.mkdir(parents=True,exist_ok=True)
    data=ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
    rows=[];plan=ResourcePlan.detect(7,1,1536,1024)
    with NativePool(data,out/'workers',plan,runtime_profile='server-one-heap')as pool:
        pending={}
        for i,case in enumerate(cases):
            for fixed in ((False,True)if i%2==0 else(True,False)):
                name=case['id']+('-fixed'if fixed else'-original');target=out/name
                advisor={**case['advisor'],'nodes':60000,'budget_ms':600000,'boss_budget_ms':600000,
                         'dop':1,'quiet_diagnostics':True,'fix_consumed_block_compensation':fixed}
                req={**case['context'],'history':case['prefix'],'generate_candidate':True,'advisor':advisor,
                     'policy_seed':case['policy_seed'],'max_decisions':len(case['prefix'])+1500,
                     'stop_at_floor':case['observation']['floor'],'capture_checkpoints':False,
                     'low_io':True,'event_driven_settle':True}
                pending[pool.submit(req,target,1800)]=(case,fixed,target)
        # Collect in submission order. Pair timing is diagnostic, not promotion.
        for future,(case,fixed,target)in pending.items():
            row={'case':case['id'],'fixed':fixed,'category':case['category'],'entry_verified':False,'replay_verified':False}
            try:
                result,identity=future.result();trace=result.get('trace',[]);evidence=result.get('decision_evidence',[])
                n=len(case['prefix'])
                row['entry_verified']=(len(evidence)>n and canonical(trace[:n])==canonical(case['prefix'])
                                      and canonical(evidence[n]['observation'])==canonical(case['observation']))
                metrics=result.get('advisor_metrics')or{}
                row.update(status=result['status'],reason=result.get('reason'),hp=(result.get('observation')or{}).get('hp'),
                           counters=metrics.get('counters',{}),differences=metrics.get('continuation_differences',[]),
                           block_compensation=metrics.get('block_compensation'),performance=result.get('performance'))
                replay_req={**case['context'],'history':trace,'generate_candidate':False,'expected_evidence':evidence,
                            'capture_checkpoints':False,'low_io':True,'event_driven_settle':True}
                replay,other=pool.run(replay_req,out/('verify-'+target.name),600,fresh=True)
                keys=['status','phase','value','observation','trace','decision_evidence','native_terminal_observed']
                # candidate horizon becomes DECISION during prescribed replay;
                # verify complete native game data without equating stop labels.
                keys.remove('status');keys.remove('phase')
                row['replay_verified']=(canonical({k:result.get(k)for k in keys})==canonical({k:replay.get(k)for k in keys})
                    and canonical(identity)==canonical(other)
                    and result['performance']['pid']!=replay['performance']['pid']
                    and replay['performance']['counters']['replay_prefix_solver_calls']==0
                    and replay['performance']['counters']['checkpoint_skipped_actions']==0)
            except Exception as error:row['error']=str(error)
            rows.append(row);write_json(out/'partial-results.json',{'rows':rows})
            print(json.dumps({'event':'fidelity_case_completed',**{k:row[k]for k in('case','fixed','entry_verified','replay_verified')}}),flush=True)
    paired=[]
    for case in cases:
        arms=[next(r for r in rows if r['case']==case['id']and r['fixed']==fixed)for fixed in(False,True)]
        paired.append({'case':case['id'],'category':case['category'],
            'mismatches':[r.get('counters',{}).get('continuation_state_mismatch',0)for r in arms],
            'matches':[r.get('counters',{}).get('continuation_state_match',0)for r in arms],
            'entry_and_replay_verified':all(r['entry_verified']and r['replay_verified']and'error'not in r for r in arms)})
    report={'complete':True,'valid':all(r['entry_and_replay_verified']for r in paired),'rows':rows,'pairs':paired,
            'selection':str(cases_file),'scope':selection['scope'],'counts_as_fresh_win':False,'normal_godot_verified':False}
    write_json(out/'report.json',report)
    print(json.dumps({'event':'fidelity_audit_completed','valid':report['valid'],'cases':len(cases)}),flush=True)
    return report['valid']


def main():
    p=argparse.ArgumentParser();p.add_argument('--prepare',type=Path);p.add_argument('--cases',type=Path)
    p.add_argument('--out',type=Path);p.add_argument('--limit',type=int,default=100);p.add_argument('--child',action='store_true');a=p.parse_args()
    if a.prepare:prepare(a.prepare);return
    if not a.cases or not a.out:p.error('--cases and --out required')
    if a.child:
        from tools import limited_cli
        sys.argv=[__file__,'--out',str(a.out)]
        def action(*args,**kwargs):
            if not run(a.cases,a.out,a.limit):raise SystemExit(1)
        limited_cli.runpy.run_module=action;limited_cli.main()
    else:p.error('Use --child inside the recorded resource-limited protocol driver')

if __name__=='__main__':main()
