"""Persistent isolated native process pool. Only owned processes may be stopped."""
from __future__ import annotations
import heapq, json, os, queue, subprocess, threading, time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from dataclasses import dataclass
from ..canonical import ContractError
from .io import read_json,write_json
from ..native import build_host,input_fingerprint
from .resources import ResourcePlan,available_memory,process_memory,enclosing_job_memory_limit
from .identity import require_advisor_identity

class WorkerError(ContractError):pass
RUNTIME_PROFILES=('legacy','workstation-2','server-one-heap','server-bounded-heap','server-large-gen0','server-bounded-large-gen0')
MEMORY_POLICIES=('hard','recycle-at-boundary')
# Combat search allocates roughly 200 KB per expanded node and almost all of it
# dies young. A 1 GiB gen0 budget cut GC pauses from 1.89 s to 1.07 s per boss
# search and raised nodes/s by 7 % under seven concurrent workers, with
# identical game results, for about 270 MiB more working set per worker.
LARGE_GEN0_BYTES=1<<30


class PriorityWorkQueue:
    """Physical priority only; returned futures still identify original requests.

    A standard bounded executor runs dequeue operations. Canceled requests never
    invoke their function, and shutdown cancels all remaining public futures.
    """
    def __init__(self,workers):
        self.executor=ThreadPoolExecutor(max_workers=workers)
        self.lock=threading.Lock();self.pending=[];self.serial=0;self.closed=False
    def submit(self,priority,fn,*args,**kwargs):
        future=Future()
        with self.lock:
            if self.closed:raise RuntimeError('Work queue is closed')
            item=(priority,self.serial,future,fn,args,kwargs)
            heapq.heappush(self.pending,item);self.serial+=1
            try:self.executor.submit(self._next)
            except BaseException:
                self.pending.remove(item);heapq.heapify(self.pending);future.cancel();raise
        return future
    def _next(self):
        with self.lock:
            if not self.pending:return
            _,_,future,fn,args,kwargs=heapq.heappop(self.pending)
        if not future.set_running_or_notify_cancel():return
        try:result=fn(*args,**kwargs)
        except BaseException as error:future.set_exception(error)
        else:future.set_result(result)
    def shutdown(self,wait=True,cancel_futures=False):
        with self.lock:
            self.closed=True
            pending=[item[2]for item in self.pending]if cancel_futures else[]
            if cancel_futures:self.pending.clear()
        # Future callbacks may themselves submit or inspect this queue.
        for future in pending:future.cancel()
        self.executor.shutdown(wait=wait,cancel_futures=cancel_futures)

def runtime_settings(plan,profile='legacy'):
    if profile not in RUNTIME_PROFILES:raise ValueError('Unknown native runtime profile')
    if profile!='legacy'and plan.dop!=1:raise ValueError('Experimental runtime profiles require solver DOP 1')
    processors=plan.dop if profile=='legacy'else 2
    if processors>plan.effective_cpus:raise ValueError('CLR processor count exceeds assigned CPUs')
    result={'profile':profile,'processors':processors,'server_gc':profile in('server-one-heap','server-bounded-heap','server-large-gen0','server-bounded-large-gen0'),'gc_heaps':1,'solver_dop':plan.dop}
    if profile in('server-bounded-heap','server-bounded-large-gen0'):
        result['gc_heap_hard_limit_bytes']=plan.worker_memory_bytes*3//4
    if profile in('server-large-gen0','server-bounded-large-gen0'):
        result['gc_gen0_bytes']=LARGE_GEN0_BYTES
    return result

def apply_heap_limit(env,runtime):
    env=dict(env)
    limit=runtime.get('gc_heap_hard_limit_bytes')
    if limit is not None:
        # Child-only, opt-in configuration. Per-object heap limits supersede the
        # total limit, so do not silently inherit incompatible settings.
        for prefix in('DOTNET_','COMPlus_'):
            for suffix in('', 'Percent','SOH','LOH','POH','SOHPercent','LOHPercent','POHPercent'):
                env.pop(prefix+'GCHeapHardLimit'+suffix,None)
        env['DOTNET_GCHeapHardLimit']=hex(limit)
    gen0=runtime.get('gc_gen0_bytes')
    if gen0 is not None:
        for prefix in('DOTNET_','COMPlus_'):env.pop(prefix+'GCgen0size',None)
        # The runtime parses this setting as hexadecimal without a 0x prefix.
        env['DOTNET_GCgen0size']=format(gen0,'x')
    return env


def memory_settings(policy):
    if policy not in MEMORY_POLICIES:
        raise ValueError('Unknown worker memory policy')
    limit=enclosing_job_memory_limit() if policy=='recycle-at-boundary' else None
    if policy=='recycle-at-boundary' and (limit is None or not 0<limit<=14*1024**3):
        raise ValueError('Boundary recycling requires an enforced Windows Job memory cap <= 14 GiB')
    return {'worker_memory_policy':policy,'enforced_job_memory_limit_bytes':limit}

class NativeWorker:
    def __init__(self,exe: str,assembly: Path,data: Path,directory: Path,plan: ResourcePlan,*,runtime_profile='legacy',memory_policy='hard'):
        self.exe,self.assembly,self.data,self.directory,self.plan=exe,assembly,data,directory,plan
        self.runtime=runtime_settings(plan,runtime_profile)
        self.memory=memory_settings(memory_policy)
        self.recycle_pending=False
        self.process=None;self.inbox=queue.Queue();self.jobs=0;self.peak_rss=0
        self.lock=threading.Lock();self.reader=None;self.starts=0;self.failure_metrics=None
    def start(self):
        if self.process is not None:return
        self.jobs=0;self.peak_rss=0;self.recycle_pending=False
        self.directory.mkdir(parents=True,exist_ok=True)
        env=dict(os.environ,SPIRE_GAME_DATA=str(self.data),DOTNET_CLI_TELEMETRY_OPTOUT='1',
                 DOTNET_PROCESSOR_COUNT=str(self.runtime['processors']),DOTNET_gcServer=str(int(self.runtime['server_gc'])),
                 DOTNET_GCHeapCount='1',DOTNET_GCNoAffinitize='1',DOTNET_gcConcurrent='1',
                 TMPDIR=str(self.directory),TMP=str(self.directory),TEMP=str(self.directory))
        env=apply_heap_limit(env,self.runtime)
        # Do NOT redirect the user's saves. This host installs its in-memory save store.
        self.process=subprocess.Popen([self.exe,str(self.assembly),'--worker'],cwd=self.directory,
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,
            encoding='utf-8',errors='replace',bufsize=1,env=env,start_new_session=(os.name!='nt'))
        self.starts+=1;self.inbox=queue.Queue()
        proc=self.process;inbox=self.inbox
        def read():
            try:
                with (self.directory/f'process-{proc.pid}.log').open('w',encoding='utf-8') as log:
                    for line in proc.stdout:
                        log.write(line)
                        if line.startswith('SPIRE_WORKER_'):inbox.put(line.rstrip())
                    log.flush()
            finally:inbox.put(None)
        self.reader=threading.Thread(target=read,daemon=True);self.reader.start()
        try:line=self.inbox.get(timeout=20)
        except queue.Empty:
            self.close();raise WorkerError('WORKER_START_TIMEOUT')
        if not line or not line.startswith('SPIRE_WORKER_READY '):
            self.close();raise WorkerError('WORKER_START_FAILED')
    def execute(self,request_path: Path,timeout: float=90) -> dict:
        with self.lock:
            self.failure_metrics=None
            self.start();assert self.process and self.process.stdin
            proc=self.process;start=time.perf_counter();task_peak_rss=0
            try:
                proc.stdin.write(str(request_path.resolve())+'\n');proc.stdin.flush()
                while True:
                    if time.perf_counter()-start>timeout:raise WorkerError('NATIVE_TASK_TIMEOUT')
                    rss=process_memory(proc.pid);self.peak_rss=max(self.peak_rss,rss)
                    task_peak_rss=max(task_peak_rss,rss)
                    if rss>self.plan.worker_memory_bytes:
                        if self.memory['worker_memory_policy']=='recycle-at-boundary':
                            self.recycle_pending=True
                        else:
                            self.failure_metrics={'pid':proc.pid,'rss_bytes':rss,'worker_limit_bytes':self.plan.worker_memory_bytes,
                                                  'worker_job':self.jobs+1,'runtime':self.runtime}
                            raise WorkerError('NATIVE_TASK_MEMORY_BUDGET')
                    try:line=self.inbox.get(timeout=min(.2,max(.01,timeout-(time.perf_counter()-start))))
                    except queue.Empty:continue
                    if line is None:
                        proc.wait(timeout=2)
                        raise WorkerError('NATIVE_WORKER_CRASH:exit='+str(proc.returncode))
                    if not line.startswith('SPIRE_WORKER_RESULT '):continue
                    result=json.loads(line.removeprefix('SPIRE_WORKER_RESULT '))
                    self.jobs+=1
                    if not result['healthy']:raise WorkerError('NATIVE_WORKER_FAILED: '+str(result.get('error')))
                    # Include the completion boundary: short requests may finish
                    # between periodic RSS samples.
                    rss=process_memory(proc.pid);task_peak_rss=max(task_peak_rss,rss);self.peak_rss=max(self.peak_rss,rss)
                    if rss>self.plan.worker_memory_bytes and self.memory['worker_memory_policy']=='recycle-at-boundary':
                        self.recycle_pending=True
                    return {'pid':proc.pid,'worker_job':self.jobs,'wall_seconds':time.perf_counter()-start,
                            'peak_sampled_rss':task_peak_rss,'process_peak_sampled_rss':self.peak_rss,'worker_starts':self.starts,
                            'recycle_reason':'rss_threshold' if self.recycle_pending else None,**self.memory}
            except BaseException:
                self.close();raise
    def close(self):
        proc=self.process;self.process=None
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.stdin.write('QUIT\n');proc.stdin.flush();proc.wait(timeout=2)
            except (BrokenPipeError,OSError,subprocess.TimeoutExpired):
                if proc.poll() is None:proc.kill()
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:pass
            if proc.stdin:
                try:proc.stdin.close()
                except OSError:pass
            if self.reader:self.reader.join(timeout=2)
            if proc.stdout:
                try:proc.stdout.close()
                except OSError:pass

class NativePool:
    def __init__(self,data: Path,directory: Path,resources: ResourcePlan,*,max_jobs=32,runtime_profile='legacy',queue_policy='fifo',memory_policy='hard'):
        if queue_policy not in('fifo','short-prefix-first'):raise ValueError('Unknown native queue policy')
        if max_jobs<1:raise ValueError('max_jobs must be positive')
        self.memory=memory_settings(memory_policy)
        self.queue_policy=queue_policy
        self.data,self.directory,self.resources=data.resolve(),directory.resolve(),resources
        self.runtime=runtime_settings(resources,runtime_profile)
        self.exe,self.assembly,self.stamp=build_host(self.data)
        self.binary_inputs=input_fingerprint(self.data)
        self.inputs={**self.binary_inputs,'native_runtime':self.runtime};self.max_jobs=max_jobs
        self.workers=[NativeWorker(self.exe,self.assembly,self.data,self.directory/f'worker-{i}',resources,runtime_profile=runtime_profile,memory_policy=memory_policy)
                      for i in range(resources.workers)]
        self.available=queue.Queue()
        for worker in self.workers:self.available.put(worker)
        self.executor=(ThreadPoolExecutor(max_workers=resources.workers)if queue_policy=='fifo'else PriorityWorkQueue(resources.workers))
        self.stats={'submitted':0,'completed':0,'failed':0,'queue_seconds':0.0,'queue_policy':queue_policy,
                    'memory_recycles':0,'max_jobs':max_jobs,**self.memory}
        self.stats_lock=threading.Lock();self.cancelled=threading.Event()
    def validate_inputs(self,request: dict):
        if input_fingerprint(self.data)!=self.binary_inputs:raise WorkerError('INPUTS_CHANGED')
        if request.get('advisor'):
            try:require_advisor_identity(request['advisor'])
            except ContractError as error:raise WorkerError(str(error))
    def run(self,request: dict,output: Path,timeout=90,*,fresh=False,disposable=False,_submitted_at=None) -> tuple[dict,dict]:
        # disposable: the request runs in a new process that is closed afterwards
        # (synthetic probes must never share a process with real evaluations).
        fresh=fresh or disposable
        if request.get('probe')and not disposable:raise WorkerError('PROBE_REQUIRES_DISPOSABLE_WORKER')
        if self.cancelled.is_set():raise WorkerError('SEARCH_CANCELLED')
        submitted=time.perf_counter() if _submitted_at is None else _submitted_at
        if output.exists() and any(output.iterdir()):raise WorkerError('output must be new: '+str(output))
        output.mkdir(parents=True,exist_ok=True)
        request={**request,'command':'replay','compact':True,'out':str((output/'data').resolve())}
        write_json(output/'request.json',request,compact=bool(request.get('low_io')))
        with self.stats_lock:self.stats['submitted']+=1
        try:worker=self.available.get(timeout=max(.001,timeout-(time.perf_counter()-submitted)))
        except queue.Empty:raise WorkerError('QUEUE_TIMEOUT')
        try:
            worker.failure_metrics=None
            wait=time.perf_counter()-submitted
            if wait>=timeout:raise WorkerError('QUEUE_TIMEOUT')
            # Validate source/game inputs before dispatch; no stale cache across changes.
            try:self.validate_inputs(request)
            except WorkerError:
                worker.close();raise
            if available_memory()<self.resources.reserve_bytes:raise WorkerError('MEMORY_ADMISSION_DENIED')
            if fresh or worker.jobs>=self.max_jobs:worker.close();worker.jobs=0
            transport=worker.execute(output/'request.json',timeout-wait)
            decision=read_json(output/'data/decision.json')
            identity=read_json(output/'data/identity.json')
            if identity['host_sha256']!=self.stamp['host_sha256']:raise WorkerError('HOST_IDENTITY_CHANGED')
            for key,name in [('game_sha256','sts2.dll'),('godot_sha256','GodotSharp.dll'),('harmony_sha256','0Harmony.dll')]:
                if identity.get(key)!=self.inputs['dependencies'][name]:raise WorkerError('GAME_IDENTITY_CHANGED')
            write_json(output/'transport.json',{**transport,'queue_seconds':wait})
            with self.stats_lock:
                self.stats['completed']+=1;self.stats['queue_seconds']+=wait
            return decision,identity
        except BaseException as e:
            if self.cancelled.is_set():e=WorkerError('SEARCH_CANCELLED_AFTER_VERIFIED_WIN')
            with self.stats_lock:self.stats['failed']+=1
            write_json(output/'failure.json',{'status':'UNKNOWN','error':str(e),'game_equivalence_verified':False,
                                             'memory_failure':worker.failure_metrics})
            raise e
        finally:
            # Persist result/identity/transport before recycling and releasing
            # the worker. No subsequent request may use an over-threshold process.
            if worker.recycle_pending:
                worker.close();worker.recycle_pending=False
                with self.stats_lock:self.stats['memory_recycles']+=1
            elif disposable:
                worker.close()
                with self.stats_lock:self.stats['disposable_runs']=self.stats.get('disposable_runs',0)+1
            self.available.put(worker)
    def run_probes(self,requests: list,output: Path,timeout=90,*,_submitted_at=None) -> list:
        """Synthetic probes, one after another, in ONE process that is started
        for them and closed afterwards. Returns [(decision, None) | (None, error)]
        in request order; a probe that fails leaves the others to run."""
        if not requests or any(not r.get('probe')for r in requests):raise WorkerError('PROBE_BATCH_NEEDS_PROBE_REQUESTS')
        if self.cancelled.is_set():raise WorkerError('SEARCH_CANCELLED')
        submitted=time.perf_counter() if _submitted_at is None else _submitted_at
        if output.exists() and any(output.iterdir()):raise WorkerError('output must be new: '+str(output))
        output.mkdir(parents=True,exist_ok=True)
        try:worker=self.available.get(timeout=max(.001,timeout-(time.perf_counter()-submitted)))
        except queue.Empty:raise WorkerError('QUEUE_TIMEOUT')
        rows=[]
        try:
            worker.close();worker.jobs=0
            for index,request in enumerate(requests):
                folder=output/f'probe-{index:03d}';folder.mkdir()
                request={**request,'command':'replay','compact':True,'out':str((folder/'data').resolve())}
                write_json(folder/'request.json',request,compact=bool(request.get('low_io')))
                with self.stats_lock:self.stats['submitted']+=1
                try:
                    if self.cancelled.is_set():raise WorkerError('SEARCH_CANCELLED')
                    remaining=timeout-(time.perf_counter()-submitted)
                    if remaining<=0:raise WorkerError('NATIVE_TASK_TIMEOUT')
                    self.validate_inputs(request)
                    if available_memory()<self.resources.reserve_bytes:raise WorkerError('MEMORY_ADMISSION_DENIED')
                    transport=worker.execute(folder/'request.json',remaining)
                    decision=read_json(folder/'data/decision.json')
                    if read_json(folder/'data/identity.json')['host_sha256']!=self.stamp['host_sha256']:raise WorkerError('HOST_IDENTITY_CHANGED')
                    write_json(folder/'transport.json',transport)
                    with self.stats_lock:self.stats['completed']+=1
                    rows.append((decision,None))
                except (WorkerError,OSError,ValueError,KeyError) as error:
                    with self.stats_lock:self.stats['failed']+=1
                    write_json(folder/'failure.json',{'status':'UNKNOWN','error':str(error),'synthetic':True,'game_equivalence_verified':False})
                    rows.append((None,str(error)))
            return rows
        finally:
            worker.close();worker.recycle_pending=False
            with self.stats_lock:self.stats['disposable_runs']=self.stats.get('disposable_runs',0)+1
            self.available.put(worker)
    def submit_probes(self,requests,output,timeout):
        if self.queue_policy=='short-prefix-first':
            return self.executor.submit(len(requests[0].get('history',[])),self.run_probes,requests,output,timeout,_submitted_at=time.perf_counter())
        return self.executor.submit(self.run_probes,requests,output,timeout,_submitted_at=time.perf_counter())
    def submit(self,*args,**kwargs):
        kwargs['_submitted_at']=time.perf_counter()
        if self.queue_policy=='short-prefix-first':
            request=args[0]if args else kwargs['request']
            return self.executor.submit(len(request.get('history',[])),self.run,*args,**kwargs)
        return self.executor.submit(self.run,*args,**kwargs)
    def cancel_pending(self):
        self.cancelled.set()
        for worker in self.workers:
            proc=worker.process
            if proc is not None and proc.poll() is None:
                try:proc.kill()
                except OSError:pass
    def close(self):
        self.executor.shutdown(wait=True,cancel_futures=True)
        for worker in self.workers:worker.close()
    def __enter__(self):return self
    def __exit__(self,*_):self.close()
