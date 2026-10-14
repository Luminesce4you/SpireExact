"""Evaluation layer: request construction, dispatch to native workers, ordered
collection, result cache, checkpoint archive, ledger records and win verification."""
from __future__ import annotations
from copy import deepcopy
from time import perf_counter
import json
import hashlib,time
from ..canonical import canonical, ContractError
from ..mode1 import check_winning_replay,is_winning_candidate
from .io import read_json,write_json
from .archive import CheckpointArchive,ResultCache,classify_failure
from .pool import WorkerError
from .runtime_metrics import snapshot as runtime_snapshot
from .policies import NATIVE,POLICIES,merge as merge_tiers
from .probes import probe_outcome


class Evaluator:
    # Defaults also apply to existing evaluator adapters which provide their own
    # constructor. Scoped diagnostics override them on the instance.
    scope_prefix=None
    stop_floor=None
    # Optional callable(serial) -> policy tiers, sampled when a request is dispatched.
    prior_provider=None
    # Every time reading of the search goes through this clock, so a recorded
    # run can be replayed against its own completion times.
    clock=staticmethod(perf_counter)
    def __init__(self,pool,ctx,directory,config,advisor=None,*,scope_prefix=None,stop_floor=None):
        self.scope_prefix=deepcopy(scope_prefix) if scope_prefix is not None else None
        self.stop_floor=stop_floor
        if stop_floor is not None and (type(stop_floor)is not int or stop_floor<1):raise ValueError('Positive diagnostic stop floor required')
        self.pool,self.ctx,self.directory,self.config,self.advisor=pool,ctx,directory,config,advisor
        self.cache=ResultCache(config.cache_mib*1024**2);self.checkpoints=None
        self.identity=None;self.serial=0;self.records=[];self.started=self.clock()
        self.deadline=self.started+config.seconds
        self.inflight={};self.ready=[]
    def load_archive(self,paths):
        for path in paths:
            try:
                node=read_json(path,resolve_checkpoint=False);identity=node['payload']['identity']
                if identity.get('host_sha256')!=self.pool.stamp['host_sha256']:continue
                if any(identity.get(k)!=self.pool.inputs['dependencies'][f] for k,f in
                    [('game_sha256','sts2.dll'),('godot_sha256','GodotSharp.dll'),('harmony_sha256','0Harmony.dll')]):continue
                if self.checkpoints is None:
                    self.identity=identity
                    self.checkpoints=CheckpointArchive(self.ctx,identity,self.config.checkpoint_mib*1024**2)
                self.checkpoints.add(path)
            except (ValueError,KeyError,OSError):continue
    def request(self,prefix,*,policy=0,horizon=None,actions=None,advisor_scale=1,boss_budget_ms=None,advisor_patch=None):
        if self.scope_prefix is not None and canonical(prefix[:len(self.scope_prefix)])!=canonical(self.scope_prefix):
            raise ContractError('Request escaped the immutable diagnostic scope prefix')
        if self.stop_floor is not None:horizon=self.stop_floor if horizon is None else min(horizon,self.stop_floor)
        request={k:self.ctx[k] for k in ('seed','character','ascension','unlocks')}
        derived=policy if self.config.solver_seed==0 else int.from_bytes(hashlib.sha256(f'{self.config.solver_seed}:{policy}'.encode()).digest()[:4],'big')&0x7fffffff
        request.update(history=prefix,generate_candidate=True,policy_seed=derived,
            max_decisions=actions or self.config.max_decisions,capture_checkpoints=True,low_io=self.config.low_io)
        if self.config.event_driven_settle:request['event_driven_settle']=True
        if horizon is not None:request['stop_at_floor']=horizon
        if self.advisor:
            request['advisor']=deepcopy(self.advisor)
            # Native paired audit: prevent a second grant after thorns spent
            # this card's block. Explicit false remains available for controls.
            request['advisor'].setdefault('fix_consumed_block_compensation',True)
            if self.config.low_io:request['advisor'].setdefault('quiet_diagnostics',True)
            request['advisor']['budget_ms']*=advisor_scale
            request['advisor']['boss_budget_ms']*=advisor_scale
            # Count-budget runs must scale the actual search budget as well.
            # Previously A10 probes only raised an already nonbinding wall cap.
            if request['advisor'].get('nodes')is not None:
                nodes=request['advisor']['nodes']*advisor_scale
                if nodes>2_147_483_647:raise ContractError('COMBAT_NODE_BUDGET_EXCEEDS_NATIVE_INTEGER_RANGE')
                request['advisor']['nodes']=nodes
            if boss_budget_ms is not None:request['advisor']['boss_budget_ms']=boss_budget_ms
            if advisor_patch:request['advisor'].update(deepcopy(advisor_patch))
        return request
    @property
    def pending_count(self):return len(self.inflight)+len(self.ready)

    def tiers_for(self,spec):
        """`policy_prior` of one generated request: the table of the lineage's
        root policy plus the tiers learned on this seed. Sampled at dispatch so
        the learned part uses every result absorbed so far; (solver_seed,
        serial) keeps the draw reproducible."""
        learned=self.prior_provider(self.serial) if self.prior_provider is not None else {}
        return merge_tiers(POLICIES[spec.get('family')or NATIVE],learned or {})

    def record(self,record):
        record.setdefault('completed_wall_seconds',self.clock()-self.started)
        if 'runtime'not in record:record['runtime']=runtime_snapshot()
        self.records.append(record)
        with (self.directory/'evaluations.jsonl').open('a',encoding='utf-8')as ledger:
            ledger.write(json.dumps(record,ensure_ascii=False,allow_nan=False)+'\n')

    def dispatch(self,specs):
        submitted=0
        for spec in specs:
            if self.serial>=self.config.evaluations or self.clock()>=self.deadline:break
            request=spec['request'];kind=spec['kind']
            if kind=='gate_probe':
                # Synthetic: one disposable worker for the whole batch, no cache, no checkpoint.
                label=f'eval-{self.serial:04d}-{kind}';self.serial+=1;submitted+=1
                timeout=max(.1,min(self.config.task_seconds,self.deadline-self.clock()))
                future=self.pool.submit_probes(spec['requests'],self.directory/label,timeout)
                self.inflight[future]=(spec,None,label,self.directory/label,None)
                continue
            if request.get('generate_candidate'):
                tiers=self.tiers_for(spec)
                if tiers:request['policy_prior']=tiers
            self.pool.validate_inputs(request)
            key=canonical({'inputs':self.pool.inputs,'request':request})
            cached=self.cache.get(key)
            label=f'eval-{self.serial:04d}-{kind}';self.serial+=1;submitted+=1
            out=self.directory/label
            if cached is not None:
                result,identity=cached
                record={'label':label,'kind':kind,'cache_hit':True,'prefix_length':len(request['history']),
                        'classification':classify_failure(result),'observation':result.get('observation')}
                if spec.get('family'):record['family']=spec['family']
                out.mkdir(parents=True);write_json(out/'cached.json',{'request':request,'result':result,'identity':identity})
                self.record(record)
                self.ready.append((spec,result,label));continue
            request=deepcopy(request)
            cp=self.checkpoints.nearest(request['history']) if self.checkpoints else None
            if cp:request['checkpoint']=str(cp.path)
            timeout=max(.1,min(self.config.task_seconds,self.deadline-self.clock()))
            future=self.pool.submit(request,out,timeout)
            self.inflight[future]=(spec,key,label,out,cp)
        return submitted

    def collect(self,timeout=.2,ordered=False):
        from concurrent.futures import wait,FIRST_COMPLETED,CancelledError
        def order(label):return int(label.split('-')[1])
        target=None
        if ordered:
            candidates=[(order(t[2]),'ready',t)for t in self.ready]
            candidates += [(order(v[2]),'future',f)for f,v in self.inflight.items()]
            if not candidates:return []
            _,kind,item=min(candidates,key=lambda x:x[0])
            if kind=='ready':self.ready.remove(item);return [item]
            target=item;outputs=[]
        else:outputs=self.ready;self.ready=[]
        if not outputs and self.inflight:
            wait([target]if target is not None else self.inflight,timeout=timeout,return_when=FIRST_COMPLETED)
        finished=[f for f in self.inflight if f.done()and(target is None or f is target)]
        # Order simultaneously available completions by submission label. A slow
        # earlier task cannot prevent replenishing the other worker lanes.
        finished.sort(key=lambda f:order(self.inflight[f][2]))
        for future in finished:
            spec,key,label,out,cp=self.inflight.pop(future)
            if spec['kind']=='gate_probe':
                outputs.append((spec,self.collect_probes(spec,future,label),label));continue
            try:result,identity=future.result()
            except (WorkerError,OSError,ValueError,CancelledError) as e:
                result={'status':'UNKNOWN','reason':str(e) or 'SEARCH_CANCELLED','trace':[], 'decision_evidence':[],
                        'native_terminal_observed':False,'value':None,'game_equivalence_verified':False}
                identity=None
            if identity:
                if self.identity is None:
                    self.identity=identity
                    self.checkpoints=CheckpointArchive(self.ctx,identity,self.config.checkpoint_mib*1024**2)
                if canonical(identity)!=canonical(self.identity):raise ContractError('worker binary identity mismatch')
                self.checkpoints.import_result(result)
                if classify_failure(result) not in ('UNKNOWN','MECHANISM_OR_HOST_GAP','RESTORE_OR_REPLAY_MISMATCH','TIMEOUT','RESOURCE_LIMIT','NATIVE_CRASH','INVALID_STATE','SEARCH_CANCELLED'):
                    self.cache.put(key,(result,identity))
            record={'label':label,'kind':spec['kind'],'cache_hit':False,'prefix_length':len(spec['request']['history']),
                'completed_wall_seconds':self.clock()-self.started,'coordinator_cpu_seconds':time.process_time(),
                'runtime':runtime_snapshot(),
                'expanded_combat_nodes':sum(int(s.get('expanded_nodes')or 0)for s in (result.get('advisor_metrics')or{}).get('searches',[])),
                'checkpoint_prefix':cp.prefix_length if cp else 0,'classification':classify_failure(result),
                'reason':result.get('reason'),'actions':len(result.get('trace',[])),
                'observation':result.get('observation'),'performance':result.get('performance'),
                'advisor_metrics':result.get('advisor_metrics'),'repair':spec.get('repair')}
            if spec.get('family'):record['family']=spec['family']
            self.record(record);outputs.append((spec,result,label))
            # Append one record rather than rewriting the entire growing ledger.
        return outputs

    def collect_probes(self,spec,future,label):
        """Outcomes of one synthetic probe batch. Nothing of it reaches the
        checkpoint archive, the result cache or a trajectory: only the reduced
        outcomes are handed on, for the gate model."""
        from concurrent.futures import CancelledError
        try:rows=future.result()
        except (WorkerError,OSError,ValueError,CancelledError) as e:rows=[(None,str(e) or 'SEARCH_CANCELLED')]*len(spec['requests'])
        outcomes=[probe_outcome(decision) if decision is not None else None for decision,_ in rows]
        usable=[o for o in outcomes if o is not None]
        self.record({'label':label,'kind':'gate_probe','cache_hit':False,'prefix_length':len(spec['request']['history']),
            'completed_wall_seconds':self.clock()-self.started,'coordinator_cpu_seconds':time.process_time(),
            'classification':'SYNTHETIC_PROBE','synthetic':True,'gate':list(spec['gate']),'table':spec['table'],'base':spec['base'],
            'probes':len(rows),'usable_probes':len(usable),'errors':[e for _,e in rows if e][:3],
            'expanded_combat_nodes':sum(o['nodes']for o in usable),'probe_search_seconds':sum(o['search_seconds']for o in usable),
            'observation':None})
        return {'status':'PROBE_BATCH','synthetic':True,'outcomes':outcomes,'trace':[],'decision_evidence':[]}

    def batch(self,specs):
        if self.pending_count:raise ContractError('batch cannot overlap streaming requests')
        self.dispatch(specs);outputs=[]
        while self.pending_count:outputs.extend(self.collect())
        return sorted(outputs,key=lambda item:int(item[2].split('-')[1]))

    def cancel_remaining(self):
        if not self.inflight:return
        self.pool.cancel_pending()
        while self.pending_count:self.collect()

    def verify_win(self,result,label):
        if not is_winning_candidate(result):return None
        out=self.directory/('verify-'+label)
        req={k:self.ctx[k] for k in ('seed','character','ascension','unlocks')}
        req.update(command='replay',out=str((out/'data').resolve()),history=result['trace'],
            generate_candidate=False,compact=True,expected_evidence=result['decision_evidence'])
        # Fresh owned process within the SAME resource reservation.
        # Starts from INITIAL run: no advisor, no checkpoint, no result-cache.
        try:
            replay,identity=self.pool.run(req,out,self.config.task_seconds,fresh=True)
            certificate=check_winning_replay(result,replay,{'context':self.ctx,'native':self.identity},
                {'context':self.ctx,'native':identity})
            write_json(self.directory/'certificate.json',certificate)
            write_json(self.directory/'winning-route.json',{'context':self.ctx,'trace':result['trace']})
            return certificate
        except (WorkerError,ContractError,OSError) as e:
            write_json(out/'verification-failed.json',{'error':str(e),'status':'UNKNOWN'});return None
