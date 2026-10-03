"""Failure-directed macro search with native short-horizon action comparisons.

Not an exact enumerator: capped actions, archive eviction, rollout utility and
beam budgets are heuristic. All unexcluded runs retain Boolean upper bound 1.
"""
from __future__ import annotations
from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
import json
import hashlib,time
from ..canonical import canonical, ContractError
from ..mode1 import OBJECTIVE,check_winning_replay,is_winning_candidate
from .io import read_json,write_json
from .archive import (CheckpointArchive,ResultCache,DiverseFrontier,utility,
                      decision_groups,classify_failure,failure_combat_prefix,combat_loss_progress)
from .pool import NativePool,NativeWorker,WorkerError
from .resources import ResourcePlan
from .runtime_metrics import snapshot as runtime_snapshot
from .policies import NATIVE,POLICIES,merge as merge_tiers,resolve as resolve_policies
from .probes import gate_entries,probe_request,probe_outcome,edit_candidates,offered_cards

@dataclass(frozen=True)
class SearchConfig:
    evaluations: int=24
    seconds: int=600
    task_seconds: int=90
    max_decisions: int=2000
    lookahead_actions: int=80
    lookahead_floors: int=2
    alternatives: int=6
    survivors: int=2
    max_repair_window: int=32
    archive_entries: int=48
    checkpoint_mib: int=512
    cache_mib: int=64
    scheduler: str='weighted'
    dispatch_mode: str='stream'
    repair_mode: str='fifo'
    boss_repair_ms: int=30000
    root_portfolio: int=1
    solver_seed: int=0
    low_io: bool=False
    dispatch_window: int=56
    snapshot_stride: int=7
    event_driven_settle: bool=False
    # focus scheduler: trajectories sharing the focus share, trajectories kept
    # with full decision sites, focus evaluations per explorer evaluation, and
    # the explorer's per-menu deferral for very wide menus.
    focus_elites: int=8
    focus_pool: int=48
    focus_share: int=3
    site_cap: int=12
    # Send the per-seed gate-model tiers with every generated rollout.
    prior: bool=False
    # repair_mode 'gate': advisor patches tried in order on a fatal fight entry,
    # best trajectory first. Each patch is plain request data.
    gate_retry_plans: tuple=()
    # A fatal fight is retried only when it was close: a life of an enemy was
    # already removed, or at least this percentage of the current enemy HP.
    # Heavier plans rescued 13 of 15 rescuable retained boss entries at 50 and
    # none of the fights below it (iteration-037 component evidence).
    gate_retry_percent: int=50
    # Extra root rollout policies (policies.POLICIES, besides 'native'): each
    # gets one fresh rollout next to the baseline while the other workers are
    # idle, and every evaluation derived from a trajectory keeps that
    # trajectory's policy table. Diversity of deck families, allocation only.
    root_policies: tuple=()
    # focus scheduler: at most this many elites whose final decks are alike
    # (0 = no limit, the historical behaviour), and the multiset-Jaccard
    # percentage from which two decks count as alike.
    focus_cluster_cap: int=0
    focus_cluster_percent: int=75
    # focus scheduler, iteration-051 (both off by default). focus_family: an
    # elite's share is its rank share times the optimistic pass rate of its
    # deck family at the gate it still has to pass (GateModels.family: every
    # real entry with an alike deck, normalised by attempts). focus_optimism:
    # percentage of one standard error added to an option's value for items the
    # gate model has seen little of (GateModels.novelty). Allocation only.
    focus_family: bool=False
    focus_optimism: int=0
    # focus scheduler, iteration-054 (off by default): elite places alternate
    # between the pool order and the same order with deaths in a later boss of
    # an act ranked by the HP carried into that fight. Allocation only.
    focus_carry: bool=False
    # Synthetic gate probes (probes.py): when a boss has this many distinct real
    # entries, one table of one-card edits (add the cards this seed offers most,
    # remove / upgrade each card of the deck) is probed from the lost entry that
    # came closest; one table per number, () = off. The probes run in disposable
    # workers, never become trajectories and only feed the gate model's values.
    gate_probe_schedule: tuple=()
    gate_probe_cards: int=40
    gate_probe_chunk: int=8
    # Advisor patch for the probe fights; None keeps the run's own gate plan.
    gate_probe_plan: dict|None=None
    def __post_init__(self):
        if any(type(v) is not int or v<1 for k,v in self.__dict__.items() if k not in ('scheduler','dispatch_mode','repair_mode','solver_seed','low_io','event_driven_settle','prior','gate_retry_plans','gate_retry_percent','root_policies','focus_cluster_cap','focus_cluster_percent','focus_family','focus_optimism','focus_carry','gate_probe_schedule','gate_probe_plan')):
            raise ValueError('all search budgets must be positive integers')
        schedule=self.gate_probe_schedule
        if type(schedule) not in (tuple,list) or any(type(n) is not int or n<1 for n in schedule) or list(schedule)!=sorted(set(schedule)):
            raise ValueError('gate probe schedule must be increasing positive entry counts')
        if self.gate_probe_plan is not None and type(self.gate_probe_plan) is not dict:raise ValueError('gate probe plan must be an advisor patch')
        if type(self.gate_retry_percent) is not int or not 0<=self.gate_retry_percent<=100:raise ValueError('gate retry percent must be an integer within 0..100')
        if type(self.root_policies) not in (tuple,list) or resolve_policies(self.root_policies)!=tuple(self.root_policies):
            raise ValueError('root policies must be distinct known policy names other than native')
        if type(self.focus_cluster_cap) is not int or self.focus_cluster_cap<0:raise ValueError('focus cluster cap must be a nonnegative integer')
        if type(self.focus_cluster_percent) is not int or not 1<=self.focus_cluster_percent<=100:raise ValueError('focus cluster percent must be an integer within 1..100')
        if type(self.focus_family) is not bool:raise ValueError('focus family must be a boolean')
        if type(self.focus_carry) is not bool:raise ValueError('focus carry must be a boolean')
        if type(self.focus_optimism) is not int or not 0<=self.focus_optimism<=400:raise ValueError('focus optimism must be an integer percentage within 0..400')
        if self.scheduler not in ('weighted','dfs','tree','incumbent','focus'):raise ValueError('unknown strategic scheduler')
        if self.dispatch_mode not in ('batch','stream','ordered'):raise ValueError('unknown dispatch mode')
        if self.repair_mode not in ('fifo','deep_boss','deep_final_boss','gate'):raise ValueError('unknown repair mode')
        if type(self.prior) is not bool:raise ValueError('prior must be a boolean')
        if type(self.gate_retry_plans) not in (tuple,list) or any(type(p) is not dict for p in self.gate_retry_plans):
            raise ValueError('gate retry plans must be a sequence of advisor patches')
        if self.focus_pool<self.focus_elites:raise ValueError('focus pool must hold every elite')

def near_miss(result,percent):
    """Allocation signal only: was the fatal fight close enough to re-solve?"""
    progress=combat_loss_progress(result)
    if progress.get('revivals_observed',0)>0:return True
    fraction=progress.get('current_life_hp_removed_fraction')
    return bool(progress.get('available')) and fraction is not None and 100*fraction>=percent

class Evaluator:
    # Defaults also apply to existing evaluator adapters which provide their own
    # constructor. Scoped diagnostics override them on the instance.
    scope_prefix=None
    stop_floor=None
    # Optional callable(serial) -> policy tiers, sampled when a request is dispatched.
    prior_provider=None
    def __init__(self,pool,ctx,directory,config,advisor=None,*,scope_prefix=None,stop_floor=None):
        self.scope_prefix=deepcopy(scope_prefix) if scope_prefix is not None else None
        self.stop_floor=stop_floor
        if stop_floor is not None and (type(stop_floor)is not int or stop_floor<1):raise ValueError('Positive diagnostic stop floor required')
        self.pool,self.ctx,self.directory,self.config,self.advisor=pool,ctx,directory,config,advisor
        self.cache=ResultCache(config.cache_mib*1024**2);self.checkpoints=None
        self.identity=None;self.serial=0;self.records=[];self.started=perf_counter()
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
        record.setdefault('completed_wall_seconds',perf_counter()-self.started)
        if 'runtime'not in record:record['runtime']=runtime_snapshot()
        self.records.append(record)
        with (self.directory/'evaluations.jsonl').open('a',encoding='utf-8')as ledger:
            ledger.write(json.dumps(record,ensure_ascii=False,allow_nan=False)+'\n')

    def dispatch(self,specs):
        submitted=0
        for spec in specs:
            if self.serial>=self.config.evaluations or perf_counter()>=self.deadline:break
            request=spec['request'];kind=spec['kind']
            if kind=='gate_probe':
                # Synthetic: one disposable worker for the whole batch, no cache, no checkpoint.
                label=f'eval-{self.serial:04d}-{kind}';self.serial+=1;submitted+=1
                timeout=max(.1,min(self.config.task_seconds,self.deadline-perf_counter()))
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
            timeout=max(.1,min(self.config.task_seconds,self.deadline-perf_counter()))
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
                'completed_wall_seconds':perf_counter()-self.started,'coordinator_cpu_seconds':time.process_time(),
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
            'completed_wall_seconds':perf_counter()-self.started,'coordinator_cpu_seconds':time.process_time(),
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


def solve(ctx: dict,out: Path,pool: NativePool,config: SearchConfig,*,advisor=None,initial_prefix=None,
          import_checkpoints=(),progress=None,scope_prefix=None,stop_floor=None):
    from collections import Counter
    from .strategy import StrategicScheduler,DepthFirstScheduler,FailureAnalyzer,strategic_trace
    from .native_tree import NativeTreeScheduler
    from .incumbent import IncumbentScheduler
    from .repairs import RepairQueue,deep_repair
    from .root_portfolio import root_requests
    from .indexed_strategy import IndexedStrategicScheduler
    from .focus import FocusScheduler
    from .gatemodel import GateModels
    if ctx.get('objective')!=OBJECTIVE:raise ContractError('P5 supports mode1 full-information Boolean objective only')
    if (out/'result.json').exists():raise ContractError('use a fresh output directory')
    out.mkdir(parents=True,exist_ok=True)
    if scope_prefix is not None:
        if config.scheduler!='weighted' or config.root_portfolio!=1:raise ContractError('Diagnostic scope requires indexed weighted scheduling and a single root')
        if canonical(initial_prefix or [])!=canonical(scope_prefix):raise ContractError('Diagnostic initial prefix must equal scope prefix')
    if config.root_policies and (initial_prefix or scope_prefix is not None or stop_floor is not None or config.root_portfolio!=1):
        raise ContractError('root policies need a fresh, unscoped start and a single native root')
    ev=(Evaluator(pool,ctx,out,config,advisor) if scope_prefix is None and stop_floor is None else
        Evaluator(pool,ctx,out,config,advisor,scope_prefix=scope_prefix,stop_floor=stop_floor))
    ev.load_archive(import_checkpoints)
    frontier=DiverseFrontier(limit=config.archive_entries)
    # Per-seed gate model: fitted on this run's own native boss fights, used to
    # order options (focus scheduler) and, with config.prior, as rollout tiers.
    probing=bool(config.gate_probe_schedule)
    if probing and (scope_prefix is not None or stop_floor is not None or advisor is None):
        raise ContractError('gate probes need an unscoped search with the combat advisor')
    models=(GateModels(config.solver_seed,probes=True) if probing else GateModels(config.solver_seed)) if probing or config.prior or config.scheduler=='focus' else None
    if config.prior:ev.prior_provider=models.prior
    scheduler=(IncumbentScheduler() if config.scheduler=='incumbent' else NativeTreeScheduler(ctx) if config.scheduler=='tree' else
               DepthFirstScheduler() if config.scheduler=='dfs' else
               FocusScheduler(scope_prefix,models=models,elites=config.focus_elites,pool=config.focus_pool,
                              focus=config.focus_share,site_cap=config.site_cap,cluster_cap=config.focus_cluster_cap,
                              cluster_percent=config.focus_cluster_percent,family=config.focus_family,
                              optimism=config.focus_optimism,carry=config.focus_carry) if config.scheduler=='focus' else
               IndexedStrategicScheduler(scope_prefix))
    repairs=RepairQueue(config.repair_mode);urgent=deque();seen_probes=set();seen_rollouts=set();groups_report=[];failures=[]
    retry_levels={}
    # evaluation label -> root policy of its lineage (only labels that are not native)
    families={};portfolio=(NATIVE,*config.root_policies);restarts=0
    # Synthetic gate probes: per boss the lost real entry that came closest (the
    # base of the next table), tables already scheduled, cards this seed offered.
    prefix_trie=getattr(getattr(scheduler,'explorer',scheduler),'prefixes',None)
    if probing and prefix_trie is None:raise ContractError('gate probes need a scheduler with the exact prefix trie')
    gate_bases={};gate_tables=Counter();offered=Counter()

    def probe_specs(gate_id,table):
        """One table as evaluation-sized batches: the unedited base first, then every one-card edit."""
        base=gate_bases[gate_id]
        trace=prefix_trie.restore(base['node'])+[base['enter']]
        entry={'enter':len(trace)-1};template=ev.request([])
        candidates=[(None,[])]+edit_candidates(base['entry'],offered,config.gate_probe_cards)
        specs=[]
        for start in range(0,len(candidates),config.gate_probe_chunk):
            chunk=candidates[start:start+config.gate_probe_chunk]
            specs.append({'kind':'gate_probe','category':'probe','gate':gate_id,'table':table,'base':base['label'],
                'labels':[name for name,_ in chunk],
                'requests':[probe_request(template,trace,entry,edits,advisor_patch=config.gate_probe_plan)for _,edits in chunk],
                'request':{'history':trace[:-1],'generate_candidate':False}})
        return specs
    best=None;best_label=None;certificate=None
    lanes=max(1,getattr(pool.resources,'workers',1))
    branches=set();rooms=set();completed=0;terminal=0;fresh=0;act2=0;act3=0
    scheduled=Counter()
    result={'schema':'spire-p5-result/v1','context':ctx,'configuration':config.__dict__,
        'resources':pool.resources.as_dict(),'native_runtime':getattr(pool,'runtime',None),'status':'UNKNOWN','lower_bound':0,'upper_bound':1,
        'optimal_in_backend':False,'game_equivalence_verified':False,
        'semantic_scope':'bundled native DLL commands in patched offline TestMode',
        'information_mode':'full; NOT a first-attempt public-information evaluation',
        'heuristic_pruning':True,'exclusion_certificates':[], 'evaluations':[]}
    if scope_prefix is not None or stop_floor is not None:
        result['diagnostic_scope']={'prefix_length':len(scope_prefix or []),'stop_floor':stop_floor,'counts_as_fresh_planner_run':False}

    def absorb(items):
        nonlocal best,best_label,certificate,completed,terminal,fresh,act2,act3
        for spec,candidate,label in items:
            if spec['kind']=='gate_probe':
                # Synthetic outcomes go to the gate model's probe tables and nowhere else.
                models.add_probes(spec['gate'],spec['table'],list(zip(spec['labels'],candidate['outcomes'])))
                if progress:progress({'label':label,'kind':'gate_probe','classification':'SYNTHETIC_PROBE','floor':None,'restored_prefix':0})
                continue
            classification=classify_failure(candidate)
            obs=candidate.get('observation') or {}
            family=spec.get('family')
            if family:families[label]=family
            if candidate.get('trace'):
                completed+=1;branches.add(strategic_trace(candidate));rooms.add(strategic_trace(candidate,True))
                act2+=int(obs.get('act',0)>=1);act3+=int(obs.get('act',0)>=2)
                fresh+=int(not spec['request']['history'] or spec['kind']=='root_rollout')
            terminal+=int(classification in ('NATIVE_ROUTE_DEATH','NATIVE_WIN_CANDIDATE'))
            if models is not None:
                # The parent is the trajectory this request deviated from (or
                # re-solved); the pair feeds the difference model.
                models.add(candidate,label,(spec.get('group')or spec.get('repair')or{}).get('source'))
            frontier.add(candidate,label);scheduler.add(candidate,label)
            if probing and candidate.get('trace'):
                searched=len(spec['request']['history'])
                offered.update(offered_cards(candidate,since=searched))
                for entry in gate_entries(candidate):
                    # Only fights this evaluation searched itself, and only lost ones:
                    # a table asks what would have helped the entry that came closest.
                    if entry['index']<searched or entry['lost'] is None:continue
                    known=gate_bases.get(entry['gate'])
                    if known is None or entry['lost'][0]>known['removed']:
                        gate_bases[entry['gate']]={'removed':entry['lost'][0],'node':prefix_trie.index(candidate['trace'][:entry['enter']])[-1],
                            'enter':candidate['trace'][entry['enter']],'entry':entry['entry'],'label':label}
                for gate_id,gate in sorted(models.gates.items()):
                    done=gate_tables[gate_id]
                    if done<len(config.gate_probe_schedule) and len(gate.entries)>=config.gate_probe_schedule[done] and gate_id in gate_bases:
                        gate_tables[gate_id]+=1;urgent.extend(probe_specs(gate_id,done))
            if best is None or utility(candidate)>utility(best):best,best_label=candidate,label
            if classification=='NATIVE_ROUTE_DEATH':
                probe=failure_combat_prefix(candidate)
                failures.append({'label':label,**FailureAnalyzer.analyze(candidate),
                    'entry_prefix_length':len(probe['prefix']) if probe else None})
                if probe and config.scheduler!='dfs':
                    if config.repair_mode=='gate':
                        # Re-solve the fatal fight with the next tactical plan;
                        # a fight that stays fatal moves on to the next level.
                        # Best trajectory first; a failed retry proves nothing.
                        # The exact prefix trie names the entry when the
                        # scheduler has one (full action bytes, no digest).
                        trie=getattr(getattr(scheduler,'explorer',scheduler),'prefixes',None)
                        key=trie.index(probe['prefix'])[-1] if trie is not None else canonical(probe['prefix'])
                        level=retry_levels.get(key,0)
                        if level<len(config.gate_retry_plans) and near_miss(candidate,config.gate_retry_percent):
                            retry_levels[key]=level+1
                            repairs.append({'kind':'gate_retry','category':'failure','priority':utility(candidate),'family':family,
                                'repair':{'source':label,'floor':probe['floor'],'room':probe['entry_observation'].get('room'),'level':level+1},
                                'request':ev.request(probe['prefix'],policy=0,horizon=probe['floor']+config.lookahead_floors,
                                    actions=min(config.max_decisions,len(probe['prefix'])+config.lookahead_actions),
                                    advisor_patch=config.gate_retry_plans[level])})
                    elif (key:=canonical(probe['prefix'])) not in seen_probes:
                        seen_probes.add(key)
                        deep=deep_repair(config.repair_mode,probe['entry_observation'])
                        repairs.append({'kind':'combat_probe','category':'failure','family':family,
                            'repair':{'source':label,'floor':probe['floor'],'room':probe['entry_observation'].get('room'),'deep':deep},
                            'request':ev.request(probe['prefix'],policy=0,horizon=probe['floor'],
                                actions=min(config.max_decisions,len(probe['prefix'])+config.lookahead_actions),advisor_scale=3,
                                boss_budget_ms=config.boss_repair_ms if deep else None)})
            if spec['kind']=='combat_probe' and classification in ('SEARCH_BUDGET','DECISION_BOUNDARY'):
                prefix=candidate.get('trace',[]);key=canonical(prefix)
                if prefix and len(prefix)<config.max_decisions and key not in seen_rollouts:
                    seen_rollouts.add(key);urgent.append({'kind':'probe_followup','category':'preparation','family':family,'request':ev.request(prefix)})
            if is_winning_candidate(candidate):
                certificate=ev.verify_win(candidate,label)
                if certificate:break
            if progress:progress({'label':label,'kind':spec['kind'],'classification':classification,
                'floor':obs.get('floor'),'restored_prefix':candidate.get('restored_prefix',0)})

    last_saved_records=0
    def save(force=False):
        nonlocal last_saved_records
        if not force and not certificate and len(ev.records)-last_saved_records<config.snapshot_stride:return
        last_saved_records=len(ev.records)
        invalid={'UNKNOWN','MECHANISM_OR_HOST_GAP','RESTORE_OR_REPLAY_MISMATCH','TIMEOUT','RESOURCE_LIMIT','NATIVE_CRASH','INVALID_STATE','SEARCH_CANCELLED','SYNTHETIC_PROBE'}
        metrics={**scheduler.snapshot(),'scheduled_evaluations':ev.serial,'proposed_evaluations':sum(scheduled.values()),'inflight_evaluations':ev.pending_count,
            'executed_evaluations':sum(not r.get('cache_hit') for r in ev.records),
            'valid_evaluations':sum(r.get('classification') not in invalid for r in ev.records),
            'candidates_completed':completed,'complete_games':terminal,'fresh_start_candidates':fresh,
            'unique_strategic_routes':len(branches),'unique_room_routes':len(rooms),
            'routes_reaching_act2':act2,'routes_reaching_act3':act3,
            'scheduled_by_kind':dict(scheduled),'prefix_cache_hits':sum(r.get('checkpoint_prefix',0)>0 for r in ev.records),
            'pending_repairs':len(repairs),'executed_deep_boss_repairs':sum(bool((r.get('repair')or{}).get('deep'))for r in ev.records),
            'prefix_cache_misses':sum(not r.get('cache_hit') and r.get('checkpoint_prefix',0)==0 for r in ev.records)}
        if config.root_policies:metrics['evaluations_by_root_policy']={name:sum((r.get('family')or NATIVE)==name for r in ev.records if r.get('kind')!='gate_probe')for name in portfolio}
        if probing:
            batches=[r for r in ev.records if r.get('kind')=='gate_probe']
            metrics['gate_probes']={'batches':len(batches),'probes':sum(r.get('probes',0)for r in batches),'usable':sum(r.get('usable_probes',0)for r in batches),
                'tables_scheduled':{str(list(k)):v for k,v in sorted(gate_tables.items())},'search_seconds':sum(r.get('probe_search_seconds',0)for r in batches),
                'scope':'synthetic; allocation only, never a trajectory, checkpoint, cached result, bound or proof'}
        # The append-only ledger keeps every record. Live snapshots have bounded
        # record history; completion publishes the full report exactly once.
        live_tail=config.low_io and not force and len(ev.records)>64
        result.update(evaluations=ev.records[-64:]if live_tail else ev.records,
            evaluations_total=len(ev.records),evaluations_truncated=live_tail,evaluations_ledger='evaluations.jsonl',
            frontier=frontier.snapshot(),failures=failures[-64:]if live_tail else failures,failures_total=len(failures),
            lookahead_groups=groups_report[-64:]if live_tail else groups_report,lookahead_groups_total=len(groups_report),
            search_metrics=metrics,cache_hits=ev.cache.hits,
            checkpoints=ev.checkpoints.snapshot(limit=64 if live_tail else None) if ev.checkpoints else {},pool_stats=pool.stats,
            elapsed_seconds=perf_counter()-ev.started,best_label=best_label,
            best_observation=(best or {}).get('observation'),
            unresolved_regions=[{'kind':'all runs not excluded by a proof','upper_bound':1}])
        if models is not None:
            if force:models.refit()
            result['gate_models']=models.snapshot()
        if certificate:result.update(status='VERIFIED_WIN_IN_NATIVE_HOST',lower_bound=1,optimal_in_backend=True,unresolved_regions=[])
        write_json(out/'result.json',result,compact=config.low_io)

    def run(specs):
        scheduled.update(s['kind'] for s in specs)
        items=ev.batch(specs);absorb(items);save();return items

    if config.root_portfolio>1:
        if initial_prefix:raise ContractError('root portfolio only supports fresh initial runs')
        request=ev.request([])
        request['stop_at_strategic_decision']=True
        # A menu-only observation must not outrank dead full campaigns merely
        # because it is alive at floor zero; do not insert it into the frontier.
        scheduled['root_menu']+=1
        initial=ev.batch([{'kind':'root_menu','category':'exploration','request':request}])
        roots,deferred=root_requests(initial[0][1],ev,min(config.root_portfolio,lanes)) if initial else ([],0)
        result['root_portfolio']={'offered_branches_scheduled':len(roots),'deferred_actions':deferred,
            'source':'actual native initial menu','prior_routes_loaded':False}
        if roots:urgent.extend(roots)
        else:run([{'kind':'baseline','category':'exploration','request':ev.request([])}])
    else:
        # The baseline alone leaves every other worker idle for its whole
        # duration: the extra root policies cost no wall time.
        run([{'kind':'baseline','category':'exploration','request':ev.request(initial_prefix or [])}]+
            [{'kind':'root_policy','category':'exploration','family':name,'request':ev.request([])}for name in config.root_policies])
        if config.root_policies:result['root_policies']={'portfolio':list(portfolio),'continuation':'lineage keeps its root policy table',
            'tables_are':'diversity sources; allocation only, never legality or a bound'}
    while not certificate:
        can_schedule=ev.serial<config.evaluations and perf_counter()<ev.deadline
        window=config.dispatch_window if config.dispatch_mode=='ordered'else lanes
        free=max(0,window-ev.pending_count)
        specs=[]
        if can_schedule:
            for slot in range(min(free,config.evaluations-ev.serial)):
                if urgent:
                    specs.append(urgent.popleft());continue
                probes=(scheduled['combat_probe']+scheduled['gate_retry']
                        +sum(s['kind'] in ('combat_probe','gate_retry') for s in specs))
                if repairs and (probes==0 or probes<.2*(sum(scheduled.values())+len(specs)+1)):
                    specs.append(repairs.popleft());continue
                group=scheduler.next()
                if group:
                    group['prefix_length']=len(group['prefix'])
                    request=ev.request(group.pop('prefix'),policy=group.get('policy',0),
                        horizon=None if config.scheduler=='dfs' else group['floor']+config.lookahead_floors,
                        actions=config.max_decisions if config.scheduler=='dfs' else
                            min(config.max_decisions,group['prefix_length']+config.lookahead_actions))
                    specs.append({'kind':'macro_'+group['category'],'category':group['category'],
                                  'group':group,'family':families.get(group.get('source')),'request':request})
                    groups_report.append(group)
                else:
                    # Restarts rotate through the root policies (native only without a portfolio).
                    specs.append({'kind':'restart','category':'exploration','family':portfolio[restarts%len(portfolio)]if restarts%len(portfolio)else None,
                                  'request':ev.request(scope_prefix or [],policy=ev.serial+slot+1)})
                    restarts+=1
        if specs:
            scheduled.update(s['kind'] for s in specs);ev.dispatch(specs)
        if not ev.pending_count:break
        trials=ev.collect(ordered=True) if config.dispatch_mode=='ordered'else ev.collect()
        if config.dispatch_mode=='batch':
            while ev.pending_count:trials.extend(ev.collect())
            trials.sort(key=lambda item:int(item[2].split('-')[1]))
        if not trials:continue
        absorb(trials);save()
        if certificate:
            ev.cancel_remaining();save();break
        viable=[t for t in trials if t[0]['kind'].startswith('macro_') and
                classify_failure(t[1]) in ('SEARCH_BUDGET','DECISION_BOUNDARY')]
        for spec,candidate,label in sorted(viable,key=lambda x:utility(x[1]),reverse=True)[:config.survivors]:
            prefix=candidate.get('trace',[]);key=canonical(prefix)
            if prefix and len(prefix)<config.max_decisions and key not in seen_rollouts:
                seen_rollouts.add(key);urgent.append({'kind':'deepen','category':'exploration','family':families.get(label),'request':ev.request(prefix)})
    result['stop_reason']='verified_boolean_maximum' if certificate else ('time_budget' if perf_counter()>=ev.deadline else 'evaluation_budget')
    save(force=True);return result
