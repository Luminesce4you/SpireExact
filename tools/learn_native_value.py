"""Offline state-value diagnostic from COMPLETED native experiments only.

No seed/RNG/trace-length features. Whole seeds, not adjacent states, define the
development split. This is a proposal model, never a game rule or win certificate.
"""
from pathlib import Path
from collections import Counter
import hashlib,json,os,sys,time
os.environ.setdefault('OPENBLAS_NUM_THREADS','8');os.environ.setdefault('OMP_NUM_THREADS','8')
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.canonical import digest
from spire_exact.planning.io import read_json,write_json

def features(obs):
    result={}
    for key in ('act','floor','hp','max_hp','gold'):
        if obs.get(key) is not None:result['number:'+key]=float(obs[key])
    for card in obs.get('deck',[]):
        key='card:'+card['id'];result[key]=result.get(key,0)+1
        if card.get('upgrade',0):result['upgraded:'+card['id']]=result.get('upgraded:'+card['id'],0)+1
    for kind in ('relics','potions'):
        for item in obs.get(kind,[]):
            if item is not None:
                key=kind+':'+str(item);result[key]=result.get(key,0)+1
    return result

def auc(y,p):
    order=np.argsort(p,kind='stable');p=p[order];y=y[order]
    total=0.;neg=0;pos=sum(y);nneg=len(y)-pos
    if not pos or not nneg:return None
    start=0
    while start<len(y):
        end=start+1
        while end<len(y) and p[end]==p[start]:end+=1
        group_pos=sum(y[start:end]);group_neg=end-start-group_pos
        total+=group_pos*(neg+.5*group_neg);neg+=group_neg;start=end
    return float(total/(pos*nneg))

def main():
    out=ROOT/'experiments/iteration-009/value-diagnostic-v2'
    out.mkdir(parents=True,exist_ok=False)
    rows=[];sources=[];counts=Counter()
    for frozen in ('frozen-i001','frozen-i002','frozen-i003','frozen-i004','frozen-i005'):
        base=ROOT/'experiments'/frozen/'experiments'
        for perf in base.glob('iteration-*/*/performance.json'):
            if not read_json(perf).get('complete'):continue
            manifest=read_json(perf.parent/'validation-manifest.json')
            sources.append({'manifest':str(perf.parent/'validation-manifest.json'),'version':manifest['version']})
            for result_path in perf.parent.glob('seed-*/result.json'):
                result=read_json(result_path);seed=result['context']['seed']
                cert_path=result_path.parent/'certificate.json'
                cert=read_json(cert_path) if cert_path.exists() else None
                negative=[e for e in result['evaluations'] if e.get('classification')=='NATIVE_ROUTE_DEATH']
                negative=sorted(negative,key=lambda e:hashlib.sha256((seed+':'+e['label']).encode()).digest())[:16]
                candidates=negative+[e for e in result['evaluations'] if e.get('classification')=='NATIVE_WIN_CANDIDATE']
                for evaluation in candidates:
                    classification=evaluation.get('classification')
                    if classification not in ('NATIVE_ROUTE_DEATH','NATIVE_WIN_CANDIDATE'):continue
                    data=result_path.parent/evaluation['label']/'data'
                    evidence=data/'decision-evidence.jsonl'
                    if not evidence.exists():continue
                    target=0
                    if classification=='NATIVE_WIN_CANDIDATE':
                        trace_file=data/'trace.jsonl'
                        if cert is None or not trace_file.exists():continue
                        trace=[json.loads(line) for line in trace_file.read_text(encoding='utf-8').splitlines()]
                        if digest(trace)!=cert['trace_sha256']:continue
                        target=1
                    candidate=[];seen=set()
                    with evidence.open(encoding='utf-8') as f:
                        for line in f:
                            # Native compact JSONL: avoid decoding combat/action noise.
                            if not line.startswith(('{"phase":"map"','{"phase":"event"','{"phase":"card_reward"','{"phase":"shop"','{"phase":"rest"')):continue
                            event=json.loads(line);obs=event['observation']
                            room=(obs.get('act'),obs.get('floor'))
                            if room in seen or float(obs.get('hp') or 0)<=0:continue
                            seen.add(room);candidate.append(features(obs))
                    if not candidate:continue
                    if len(candidate)>12:
                        candidate=[candidate[i*(len(candidate)-1)//11] for i in range(12)]
                    counts['native_trajectories']+=1;counts['verified_winning_trajectories']+=target
                    for x in candidate:
                        rows.append({'seed':seed,'x':x,'y':target,'weight':1/len(candidate)})
            print(json.dumps({'event':'dataset_source','run':manifest['run_id'],'rows':len(rows)}),flush=True)
    if not rows:raise SystemExit('No completed native data')
    with (out/'samples.jsonl').open('w',encoding='utf-8') as f:
        for row in rows:f.write(json.dumps(row,separators=(',',':'))+'\n')
    seeds=sorted({r['seed'] for r in rows})
    validation={s for s in seeds if int(hashlib.sha256(('value-dev-split-v1:'+s).encode()).hexdigest(),16)%5==0}
    train=np.array([r['seed'] not in validation for r in rows]);test=~train
    names=sorted({key for r,in_train in zip(rows,train) if in_train for key in r['x']});index={k:i for i,k in enumerate(names)}
    x=np.zeros((len(rows),len(names)),dtype=np.float64)
    for i,r in enumerate(rows):
        for k,v in r['x'].items():
            if k in index:x[i,index[k]]=v
    y=np.array([r['y'] for r in rows],dtype=np.float64);weights=np.array([r['weight'] for r in rows])
    mean=x[train].mean(axis=0);scale=x[train].std(axis=0);scale=np.maximum(scale,.1)
    x=(x-mean)/scale
    counts.update(samples=len(rows),train_seeds=len(seeds)-len(validation),validation_seeds=len(validation),features=len(names))
    if len(set(y[test]))<2 or len(set(y[train]))<2:raise SystemExit('Insufficient positive/negative seed groups; do not report a model')
    # One deliberately simple regularized model. A failed diagnostic is retained,
    # not rescued by cherry-picking seed splits or adding seed-specific features.
    w=np.zeros(len(names));intercept=0.;a=x[train];b=y[train];weight=weights[train].copy()
    positive=weight[b==1].sum();negative=weight[b==0].sum()
    weight[b==1]*=negative/positive;weight/=weight.sum()
    start=time.perf_counter()
    for iteration in range(500):
        z=np.clip(a@w+intercept,-30,30);p=1/(1+np.exp(-z));error=(p-b)*weight
        w-=.08*(a.T@error+.01*w);intercept-=.08*error.sum()
    predictions=1/(1+np.exp(-np.clip(x@w+intercept,-30,30)))
    metrics={'counts':dict(counts),'train_auc':auc(y[train],predictions[train]),
        'heldout_seed_auc':auc(y[test],predictions[test]),'training_seconds':time.perf_counter()-start,
        'heldout_seeds':sorted(validation),'source_manifests':sources,
        'sampling':'all verified winning trajectories;16 deterministically sampled losing trajectories per seed;12 evenly spaced living states per trajectory',
        'seed_rng_trace_length_features':False,'class_balanced_scores_are_not_calibrated_win_probabilities':True,
        'native_game_validation':False,'status':'DIAGNOSTIC_ONLY_NOT_DEPLOYED'}
    write_json(out/'metrics.json',metrics)
    write_json(out/'model.json',{'schema':'native-observation-logistic-value/v1','features':names,
        'mean':mean.tolist(),'scale':scale.tolist(),'coefficients':w.tolist(),'intercept':intercept,
        'usage':'heuristic priority only; not game mechanics, state identity or a proof'})
    print(json.dumps(metrics,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
