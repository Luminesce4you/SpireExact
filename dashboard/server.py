"""Local experiment UI and explicit seed submission, with SSE telemetry.

No game commands inside the HTTP service and no mutation of frozen experiments.
"""
from pathlib import Path
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs
import argparse,ctypes as c,datetime,json,mimetypes,os,queue,struct,threading,time,sys,secrets
from ctypes import wintypes as w

ROOT=Path(__file__).resolve().parents[1]
STATIC=Path(__file__).resolve().parent/'web'
sys.path.insert(0,str(ROOT))
if __name__=='__main__':sys.modules.setdefault('dashboard.server',sys.modules[__name__])
from dashboard import jobs
from dashboard.winning_route import WinningRouteStore

def shared_text(path,encoding='utf-8-sig'):
    if os.name!='nt':return path.read_text(encoding=encoding)
    # Python's ordinary file reader omits FILE_SHARE_DELETE on Windows and
    # can crash a concurrent atomic result.json replacement. Keep reading the
    # opened snapshot while allowing the solver to publish its next version.
    import msvcrt
    k=c.WinDLL('kernel32',use_last_error=True)
    k.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,c.c_void_p,w.DWORD,w.DWORD,w.HANDLE]
    k.CreateFileW.restype=w.HANDLE;k.CloseHandle.argtypes=[w.HANDLE]
    handle=k.CreateFileW(str(path),0x80000000,7,None,3,0x80,None)
    if handle in(None,c.c_void_p(-1).value):raise c.WinError(c.get_last_error())
    try:fd=msvcrt.open_osfhandle(handle,os.O_RDONLY)
    except BaseException:k.CloseHandle(handle);raise
    with os.fdopen(fd,'r',encoding=encoding)as stream:return stream.read()

def read(path,default=None):
    try:return json.loads(shared_text(path))
    except (OSError,ValueError):return default

def json_lines(path):
    try:
        rows=[]
        for line in shared_text(path,'utf-8').splitlines():
            try:rows.append(json.loads(line))
            except ValueError:pass
        return rows
    except OSError:return []

class Broker:
    def __init__(self):self.listeners=set();self.lock=threading.Lock()
    def subscribe(self):
        q=queue.Queue(16)
        with self.lock:self.listeners.add(q)
        return q
    def remove(self,q):
        with self.lock:self.listeners.discard(q)
    def publish(self,kind,payload):
        with self.lock:
            for q in self.listeners:
                try:q.put_nowait((kind,payload))
                except queue.Full:
                    while not q.empty():
                        try:q.get_nowait()
                        except queue.Empty:break
                    q.put_nowait(('resync',{}))

class ProcessSampler:
    """Read CPU and private committed memory of the owned process tree."""
    def __init__(self):self.previous={};self.total={}
    def sample(self,pid,key):
        if os.name!='nt' or not pid:return {'available':False}
        class Entry(c.Structure):
            _fields_=[('size',w.DWORD),('usage',w.DWORD),('pid',w.DWORD),('heap',c.c_size_t),('module',w.DWORD),('threads',w.DWORD),('parent',w.DWORD),('priority',c.c_long),('flags',w.DWORD),('exe',w.WCHAR*260)]
        class Memory(c.Structure):
            _fields_=[('cb',w.DWORD),('faults',w.DWORD)]+[(n,c.c_size_t)for n in ['peak_ws','ws','peak_pool','pool','peak_nonpool','nonpool','pagefile','peak_pagefile','private']]
        k=c.WinDLL('kernel32',use_last_error=True);ps=c.WinDLL('psapi')
        k.CreateToolhelp32Snapshot.argtypes=[w.DWORD,w.DWORD];k.CreateToolhelp32Snapshot.restype=w.HANDLE
        k.Process32FirstW.argtypes=[w.HANDLE,c.POINTER(Entry)];k.Process32NextW.argtypes=[w.HANDLE,c.POINTER(Entry)]
        k.CloseHandle.argtypes=[w.HANDLE];k.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];k.OpenProcess.restype=w.HANDLE
        k.GetProcessTimes.argtypes=[w.HANDLE,c.POINTER(w.FILETIME),c.POINTER(w.FILETIME),c.POINTER(w.FILETIME),c.POINTER(w.FILETIME)]
        k.GetExitCodeProcess.argtypes=[w.HANDLE,c.POINTER(w.DWORD)]
        ps.GetProcessMemoryInfo.argtypes=[w.HANDLE,c.c_void_p,w.DWORD]
        snap=k.CreateToolhelp32Snapshot(2,0);parents={}
        if snap in (None,c.c_void_p(-1).value):return {'available':False}
        e=Entry();e.size=c.sizeof(e)
        try:
            ok=k.Process32FirstW(snap,c.byref(e))
            while ok:parents[e.pid]=e.parent;ok=k.Process32NextW(snap,c.byref(e))
        finally:k.CloseHandle(snap)
        owned={pid}
        while True:
            extra={p for p,par in parents.items()if par in owned}-owned
            if not extra:break
            owned|=extra
        memory=0;delta=0;count=0;root_alive=False;current={};now=time.monotonic()
        old,then=self.previous.get(key,({},now))
        def ft(x):return ((x.dwHighDateTime<<32)|x.dwLowDateTime)
        for p in owned:
            h=k.OpenProcess(0x410,False,p)
            if not h:continue
            try:
                exitcode=w.DWORD();k.GetExitCodeProcess(h,c.byref(exitcode))
                if exitcode.value!=259:continue
                if p==pid:root_alive=True
                created=w.FILETIME();ended=w.FILETIME();kernel=w.FILETIME();user=w.FILETIME()
                if k.GetProcessTimes(h,c.byref(created),c.byref(ended),c.byref(kernel),c.byref(user)):
                    identity=(p,ft(created));cpu=(ft(kernel)+ft(user))/1e7;current[identity]=cpu
                    if identity in old:delta+=max(0,cpu-old[identity])
                m=Memory();m.cb=c.sizeof(m)
                if ps.GetProcessMemoryInfo(h,c.byref(m),c.sizeof(m)):memory+=m.private
                count+=1
            finally:k.CloseHandle(h)
        self.previous[key]=(current,now)
        return {'available':True,'alive':root_alive,'cpu_cores':delta/max(now-then,.1)if old else None,
                'private_bytes':memory,'processes':count,'sampled_at':time.time(),'scope':'sampled owned-process tree; not Job Object peak accounting'}

class Repository:
    def __init__(self):self.paths={};self.cache={};self.live={};self.history={};self.lock=threading.RLock();self.index()
    def index(self):
        paths=list((ROOT/'experiments').glob('iteration-*/*/validation-manifest.json'))
        paths+=list((ROOT/'experiments').glob('frozen-*/experiments/iteration-*/*/validation-manifest.json'))
        with self.lock:
            for p in paths:
                m=read(p)
                if m and m.get('run_id'):self.paths[m['run_id']]=p.resolve()
    def detail(self,run):
        with self.lock:
            path=self.paths.get(run)
            if not path:return None
            folder=path.parent;m=read(path,{})
            if m.get('fidelity_phases'):
                return self.fidelity_detail(run,folder,m)
            if m.get('stage')=='M0 L2 preparation diagnostic':
                return self.preparation_detail(run,folder,m)
            if m.get('tactical_arms'):
                return self.tactical_detail(run,folder,m)
            if m.get('counts_as_planner_win') is False and m.get('arms'):
                return self.component_detail(run,folder,m)
            seed=m.get('seed');target=folder/('seed-'+str(seed))if seed is not None else None
            sources=[path,folder/'performance.json',folder/'baseline-report.json',folder/'events.jsonl',folder/'validation-invalidated.json',folder/'launch-state.json']
            if target:sources += [target/'result.json',target/'evaluations.jsonl',folder/(target.name+'-resources.json'),folder/(target.name+'-events.jsonl')]
            signature=tuple((str(p),p.stat().st_mtime_ns if p.exists()else 0)for p in sources)
            if run in self.cache and self.cache[run][0]==signature:return self.cache[run][1]
            perf=read(folder/'performance.json',{});report=read(folder/'baseline-report.json',{})
            result=read(target/'result.json',{})if target else{}
            resources=read(folder/(target.name+'-resources.json'),{})if target else{}
            events=json_lines(folder/'events.jsonl')
            if target:events+=json_lines(folder/(target.name+'-events.jsonl'))
            events.sort(key=lambda e:e.get('wall_seconds',0))
            records=result.get('evaluations',[])
            if target and(target/'evaluations.jsonl').exists():
                ledger=json_lines(target/'evaluations.jsonl')
                if len(ledger)>=len(records):records=ledger
            points=[];nodes=0;furthest=0;act=0;classes={}
            for i,e in enumerate(records):
                o=e.get('observation')or{};nodes+=int(e.get('expanded_combat_nodes')or sum(int(s.get('expanded_nodes')or 0)for s in(e.get('advisor_metrics')or{}).get('searches',[])))
                floor=o.get('floor',0)or 0;furthest=max(furthest,floor);act=max(act,(o.get('act',-1)or 0)+1)
                status=e.get('classification','UNKNOWN');classes[status]=classes.get(status,0)+1
                points.append({'step':i+1,'seconds':e.get('completed_wall_seconds'),'floor':floor,'best_floor':furthest,'nodes':nodes,
                               'status':status,'label':e.get('label'),'native_cpu_seconds':((e.get('performance')or{}).get('cpu_us',0)or 0)/1e6})
            wins=perf.get('wins',int(bool(report.get('verified_win'))));complete=bool(perf.get('complete')or report)
            if result.get('status')=='VERIFIED_WIN_IN_NATIVE_HOST' and target and(target/'certificate.json').is_file():wins=1
            timestamp=m.get('timestamp');start=None
            try:start=datetime.datetime.fromisoformat(timestamp).timestamp()
            except (ValueError,TypeError):pass
            status='verified'if wins and seed is not None else'completed'if complete else'pending'
            if report and report.get('exit_code',0)not in(0,124):status='error'
            launch=read(folder/'launch-state.json',{})or{}
            if m.get('manual')and not report and launch.get('phase')in('queued','starting','error'):status=launch['phase']
            if read(folder/'validation-invalidated.json') or perf.get('valid_for_generalization_rate')is False:status='invalid'
            data={'id':run,'manifest':m,'protocol':m.get('protocol','legacy-A0'),'seed':seed,'seeds':m.get('seeds',[]),'status':status,
                  'started_at':start,'wins':wins,'tested':perf.get('tested',int(complete)),'planned':perf.get('planned',1),
                  'furthest_floor':furthest,'act':act,'nodes':nodes,'evaluations':len(records),'points':points,'classes':classes,
                  'performance':perf,'resources':resources,'report':report,'search':result.get('search_metrics',{}),
                  'pool':result.get('pool_stats',{}),'events':events[-150:],'path':str(folder),'checkpoint':result.get('checkpoints',{}),
                  'result_status':result.get('status'),'normal_godot_verified':False,'updated_at':time.time()}
            data['gate_models']=result.get('gate_models')
            data['gate_model_meta']={
                'source':str(target/'result.json') if target else None,
                'snapshot_at':(target/'result.json').stat().st_mtime if target and(target/'result.json').exists() else None,
                'elapsed_seconds':result.get('elapsed_seconds'),
                'available':isinstance(result.get('gate_models'),dict),
                'scope':'本次运行、当前种子的在线关口模型；排序与算力分配信号。'}
            self.cache[run]=(signature,data);return data
    def component_detail(self,run,folder,m):
        events=json_lines(folder/'events.jsonl')
        starts=[e for e in events if e.get('event')=='component_started']
        first=starts[0]['time'] if starts else None
        active=starts[-1] if starts else {}
        arm=active.get('arm');repeat=active.get('repeat',0)
        name=f'repeat-{repeat}-{arm}' if arm else ''
        target=folder/name
        partial=read(folder/'partial-results.json',[]) or []
        report=read(folder/'report.json',{}) or {}
        ended=next((e for e in reversed(events) if e.get('event') in ('smt_benchmark_completed','runtime_benchmark_completed','component_failed','component_paused')),None)
        job_events=json_lines(folder/(name+'-events.jsonl')) if name else []
        job_start=next((e for e in job_events if e.get('event')=='started'),{})
        cpus=job_start.get('cpus',[])
        arm_config=m['arms'].get(arm,[0,0,'unknown'])
        requests=m.get('source_requests',28)*m.get('repeated_blocks',2)
        files=list(target.glob('case-*/data/result.json')) if name else []
        finished=sum(read(p,{}).get('status')=='NATIVE_DATA_EXPORTED' for p in files)
        active_complete=any(r['arm']==arm and r['repeat']==repeat for r in partial)
        total=sum(r['component']['requests'] for r in partial)+(0 if active_complete else finished)
        planned=len(m['arms'])*m.get('repeats',2)*requests
        resources=read(folder/(name+'-resources.json'),{}) if name else {}
        points=[];nodes=0;count=0
        for e in events:
            if e.get('event')=='component_completed':
                entry=next((r for r in partial if r['arm']==e['arm'] and r['repeat']==e['repeat']),None)
                if entry:
                    count+=entry['component']['requests']
                    nodes+=sum(r['nodes'] for r in entry['component']['rows'])
                    points.append({'step':count,'seconds':e['time']-first,'best_floor':count,'nodes':nodes,'label':e['arm']})
        converted=[dict(e,component_wall_seconds=e.get('wall_seconds'),wall_seconds=e.get('time',first or 0)-(first or 0)) for e in events]
        metadata=dict(m,cpu_set=cpus,wall_cap_seconds=m.get('per_arm_wall_cap_seconds',1800),
                      settings=['--workers',str(arm_config[1]),'--dop','1','--ascension','10'])
        return {'id':run,'manifest':metadata,'kind':'component','protocol':m.get('protocol','A10 runtime component'),
                'seed':None,'seeds':[],'status':'paused' if ended and ended['event']=='component_paused' else 'error' if ended and ended['event']=='component_failed' else 'completed' if report.get('complete') else 'pending',
                'started_at':first,'wins':0,'tested':0,'planned':planned,'evaluations':total,
                'furthest_floor':0,'act':0,'nodes':nodes,'points':points,'classes':{'COMPONENT_REQUEST_COMPLETED':total},
                'performance':{},'resources':resources,'report':{'wall_seconds':ended['time']-first} if ended and first else {},
                'search':{},'pool':{},'events':converted[-150:],'path':str(folder),'checkpoint':{},
                'result_status':None,'normal_godot_verified':False,'updated_at':time.time(),
                'component':{'title':m.get('display_title'),'arm':arm,'repeat':repeat,'completed_arms':len(partial),'total_arms':len(m['arms'])*m.get('repeats',2),
                             'completed_requests':finished,'requests_per_arm':requests,'workers':arm_config[1],
                             'runtime_profile':arm_config[2],'logical_cpus':len(cpus),'physical_cores':job_start.get('physical_core_count'),
                             'smt_used':job_start.get('smt_used'),'started_at':active.get('time'),
                             'pid':job_start.get('pid'),'comparisons':report.get('comparisons',[]),'completed_runs':partial}}
    def fidelity_detail(self,run,folder,m):
        root=Path(m['cases_root']);phases=m['fidelity_phases']
        phase=next((p for p in reversed(phases)if(root/p['name']).exists()),phases[0])
        target=root/phase['name'];planned=phase['cases']*2
        report=read(target/'report.json',{})or{}
        rows=report.get('rows',(read(target/'partial-results.json',{})or{}).get('rows',[]))
        events=json_lines(root/(phase['name']+'-events.jsonl'))
        start=next((e for e in events if e.get('event')=='started'),{})
        gate=read(Path(m['gate_report']),{})or{}
        resources=read(root/(phase['name']+'-resources.json'),{})or{}
        ended=report.get('complete',False)
        status='error'if gate.get('valid')is False or report.get('valid')is False else'completed'if ended else'pending'
        verified=sum(bool(r.get('entry_verified')and r.get('replay_verified'))for r in rows)
        mismatches=sum((r.get('counters')or{}).get('continuation_state_mismatch',0)for r in rows)
        classes={}
        for row in rows:
            label=('ENTRY_DIVERGENCE'if not row.get('entry_verified')else'REPLAY_DIVERGENCE'if not row.get('replay_verified')else
                   'PREDICTION_MISMATCH_REPLAY_VERIFIED'if(row.get('counters')or{}).get('continuation_state_mismatch',0)else'REPLAY_VERIFIED')
            classes[label]=classes.get(label,0)+1
        cpus=start.get('cpus',[])
        return {'id':run,'kind':'component','manifest':dict(m,cpu_set=cpus),'protocol':'A10 fidelity audit',
                'seed':None,'seeds':[],'status':status,'started_at':start.get('time'),
                'wins':0,'tested':0,'planned':planned,'evaluations':len(rows),'furthest_floor':0,'act':0,
                'nodes':0,'points':[],'classes':classes,
                'performance':{},'resources':resources,'report':{'wall_seconds':resources.get('wall_seconds')},
                'search':{},'pool':{},'events':[],'path':str(target),'checkpoint':{},'result_status':None,
                'normal_godot_verified':False,'updated_at':time.time(),
                'component':{'title':'战斗保真 · 格挡补偿配对','description':f'{phase["name"]} · {phase["cases"]} 个真实入口 × 原版 / 修复版 · 独立重放',
                    'arm':phase['name'],'repeat':0,'completed_arms':len(rows),'total_arms':planned,
                    'completed_requests':len(rows),'requests_per_arm':planned,'workers':7,'runtime_profile':'server-one-heap',
                    'logical_cpus':len(cpus),'physical_cores':start.get('physical_core_count'),
                    'verified_replays':verified,'continuation_mismatches':mismatches,
                    'pid':start.get('pid')if not ended else None,'comparisons':[],'completed_runs':[]}}

    def tactical_detail(self,run,folder,m):
        events=json_lines(folder/'events.jsonl');case_events=json_lines(folder/'cases/events.jsonl')
        job_events=json_lines(folder/'cases-events.jsonl')
        job_start=next((e for e in job_events if e.get('event')=='started'),{})
        start=next((e['time']for e in events if e.get('event')=='tactical_diagnosis_started'),None)
        report=read(folder/'cases/report.json',{}) or {}
        partial=read(folder/'cases/partial-results.json',{}) or {}
        rows=report.get('rows',partial.get('rows',[]))
        nodes=0;points=[];classes={}
        for i,row in enumerate(rows):
            nodes+=sum(s.get('expanded_nodes',0)for s in row.get('searches',[]))
            status='TACTICAL_RESCUE_VERIFIED' if row.get('independent_replay_verified') else row['classification']
            classes[status]=classes.get(status,0)+1
            completed=next((e for e in case_events if e.get('case')==row['case']and e.get('arm')==row['arm']),{})
            points.append({'step':i+1,'seconds':completed.get('time',start or 0)-(start or 0),
                           'best_floor':i+1,'nodes':nodes,'label':row['case']+' / '+row['arm']})
        end=next((e for e in events if e.get('event')=='tactical_process_finished'),None)
        metadata=dict(m,cpu_set=job_start.get('cpus',[]),settings=['--workers',str(m['workers']),'--dop','1','--ascension','10'])
        completed_arms=sum(sum(r['arm']==arm for r in rows)==12 for arm in m['tactical_arms'])
        converted=[dict(e,wall_seconds=e.get('time',start or 0)-(start or 0))for e in events+case_events]
        return {'id':run,'kind':'component','manifest':metadata,'protocol':'A10 tactical diagnostic','seed':None,'seeds':[],
                'status':'error' if end and end['exit_code'] else 'completed' if report.get('complete') else 'pending',
                'started_at':start,'wins':0,'tested':0,'planned':36,'evaluations':len(rows),'furthest_floor':0,'act':0,
                'nodes':nodes,'points':points,'classes':classes,'performance':{},
                'resources':read(folder/'cases-resources.json',{})or{},'report':{'wall_seconds':end['time']-start}if end and start else {},
                'search':{},'pool':{},'events':sorted(converted,key=lambda e:e['wall_seconds'])[-150:],
                'path':str(folder),'checkpoint':{},'result_status':None,'normal_godot_verified':False,'updated_at':time.time(),
                'component':{'title':'首领战救援 · 节点与宽度对照','description':'12 个真实战斗入口 × 3 种搜索配置 · 救回后独立重放',
                    'arm':'Low 60k / Low 600k / VeryHigh 600k','repeat':0,'completed_arms':completed_arms,'total_arms':3,
                    'completed_requests':len(rows),'requests_per_arm':36,'workers':m['workers'],'runtime_profile':m['runtime_profile'],
                    'logical_cpus':len(job_start.get('cpus',[])),'physical_cores':job_start.get('physical_core_count'),
                    'pid':job_start.get('pid'),'comparisons':[],'completed_runs':[]}}
    def preparation_detail(self,run,folder,m):
        events=json_lines(folder/'events.jsonl');events+=json_lines(folder/'cases/events.jsonl')
        start=next((e['time']for e in events if e.get('event')=='preparation_diagnosis_started'),None)
        end=next((e for e in events if e.get('event')=='preparation_process_finished'),None)
        roots=read(folder/'cases/roots.json',[]) or []
        total_roots=len(roots)*m['repeats'];planned=total_roots*m['evaluations_per_root']
        final=read(folder/'cases/report.json',{}) or {}
        partial=read(folder/'cases/partial-results.json',{}) or {}
        rows=final.get('rows',partial.get('rows',[]))
        points=[];classes={};nodes=0;count=0
        starts=[e for e in events if e.get('event')=='preparation_root_started']
        for entry in starts:
            path=folder/'cases'/(entry['root']+f"-repeat-{entry['repeat']}")/'result.json'
            result=read(path,{}) or {}
            for r in result.get('evaluations',[]):
                count+=1;nodes+=r.get('expanded_combat_nodes',0);status=r['classification'];classes[status]=classes.get(status,0)+1
                points.append({'step':count,'seconds':entry['time']-(start or entry['time'])+r.get('completed_wall_seconds',0),
                               'best_floor':count,'nodes':nodes,'label':entry['root']+' / '+r['label']})
        job_start=next((e for e in json_lines(folder/'cases-events.jsonl')if e.get('event')=='started'),{})
        cpus=job_start.get('cpus',[]);workers=6
        metadata=dict(m,cpu_set=cpus)
        converted=[dict(e,wall_seconds=e.get('time',start or 0)-(start or 0))for e in events]
        return {'id':run,'kind':'component','manifest':metadata,'protocol':'A10 preparation diagnostic','seed':None,'seeds':[],
                'status':'error' if end and end['exit_code'] else 'completed' if final.get('complete') else 'pending',
                'started_at':start,'wins':0,'tested':0,'planned':planned,'evaluations':count,'furthest_floor':0,'act':0,
                'nodes':nodes,'points':points,'classes':classes,'performance':{},'resources':read(folder/'cases-resources.json',{})or{},
                'report':{'wall_seconds':end['time']-start}if end and start else {},'search':{},'pool':{},
                'events':sorted(converted,key=lambda e:e['wall_seconds'])[-150:],'path':str(folder),'checkpoint':{},
                'result_status':None,'normal_godot_verified':False,'updated_at':time.time(),
                'component':{'title':'本幕准备救援 · 范围固定的搜索','description':f'{total_roots} 个幕入口任务 · 每个最多 {m["evaluations_per_root"]} 次评估 · 先前幕保持不变',
                    'arm':'within-act preparation','repeat':0,'completed_arms':len(rows),'total_arms':total_roots,
                    'completed_requests':count,'requests_per_arm':planned,'workers':workers,'runtime_profile':'legacy',
                    'logical_cpus':len(cpus),'physical_cores':job_start.get('physical_core_count'),'pid':job_start.get('pid'),
                    'rescued_roots':sum(bool(r.get('first_rescue'))for r in rows),'comparisons':[],'completed_runs':[]}}
    def runs(self):
        rows=[]
        for key in list(self.paths):
            d=self.detail(key)
            if not d:continue
            live=self.live.get(key,{})
            rows.append({k:d[k]for k in ['id','protocol','seed','seeds','status','started_at','wins','tested','planned','evaluations','furthest_floor']}|{'alive':live.get('alive',False),'kind':d.get('kind','search')})
        return sorted(rows,key=lambda d:d['started_at']or 0,reverse=True)
    def snapshot(self,run=None):
        rows=self.runs()
        if run not in self.paths:run=next((r['id']for r in rows if r['alive']),next((r['id']for r in rows if r['protocol']=='A10-seed-v2'),rows[0]['id']if rows else None))
        return {'runs':rows,'selected':self.detail(run)if run else None,'live':self.live.get(run,{}),
                'telemetry_history':self.history.get(run,[]),'server_time':time.time(),'watcher':'windows-notification','read_only':False,
                'launch':{'token':jobs.TOKEN,'character':'IRONCLAD','ascension':10,'unlocks':'all',
                          'max_minutes':180 if read(ROOT/'dashboard/solver-profile.json',{}).get('long_run_ready')else 15}}

repo=Repository();broker=Broker();winning_routes=WinningRouteStore()

def watch(root):
    k=c.WinDLL('kernel32',use_last_error=True)
    k.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,c.c_void_p,w.DWORD,w.DWORD,w.HANDLE];k.CreateFileW.restype=w.HANDLE
    k.ReadDirectoryChangesW.argtypes=[w.HANDLE,c.c_void_p,w.DWORD,w.BOOL,w.DWORD,c.POINTER(w.DWORD),c.c_void_p,c.c_void_p]
    h=k.CreateFileW(str(root),1,7,None,3,0x02000000,None)
    if h in(None,c.c_void_p(-1).value):broker.publish('watch_error',{'message':'文件事件监听不可用'});return
    buffer=c.create_string_buffer(65536);length=w.DWORD()
    while True:
        if not k.ReadDirectoryChangesW(h,buffer,len(buffer),True,0x13,c.byref(length),None,None):return
        offset=0;relevant=False;reindex=False
        if length.value==0:relevant=True;reindex=True
        while offset<length.value:
            nxt,action,size=struct.unpack_from('<III',buffer.raw,offset)
            name=buffer.raw[offset+12:offset+12+size].decode('utf-16-le',errors='replace')
            base=name.replace('\\','/').split('/')[-1]
            if base in {'result.json','evaluations.jsonl','performance.json','baseline-report.json','events.jsonl','validation-manifest.json','process.json','component.json','partial-results.json','report.json','launch-state.json','solver-profile.json'}or base.endswith(('-resources.json','-events.jsonl')):relevant=True
            if base=='validation-manifest.json':reindex=True
            if not nxt:break
            offset+=nxt
        if reindex:repo.index()
        if relevant:broker.publish('files',{})

def sample_loop():
    sampler=ProcessSampler()
    while True:
        tick=time.monotonic()
        for run,path in list(repo.paths.items()):
            meta=read(path.parent/'process.json',{})
            manifest=read(path,{})
            if manifest.get('fidelity_phases')or(manifest.get('counts_as_planner_win') is False and manifest.get('arms')):
                detail=repo.detail(run)
                meta={'owned_job_pid':detail['component']['pid']} if detail['status']=='pending' else {}
                if not meta:
                    repo.live[run]={'available':True,'alive':False,'sampled_at':time.time()}
            if not meta:continue
            if (path.parent/'baseline-report.json').exists():
                if repo.live.get(run,{}).get('alive'):
                    repo.live[run]={'available':True,'alive':False,'sampled_at':time.time()}
                    broker.publish('telemetry',{'run':run,'sample':repo.live[run],'server_time':time.time()})
                continue
            sample=sampler.sample(meta.get('owned_job_pid'),run)
            repo.live[run]=sample
            if sample.get('alive'):
                rows=repo.history.setdefault(run,[]);rows.append(sample)
                if len(rows)>3600:del rows[:-3600]
            broker.publish('telemetry',{'run':run,'sample':sample,'server_time':time.time()})
        time.sleep(max(.05,1-(time.monotonic()-tick)))

class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):pass
    def send_body(self,body,kind='application/json; charset=utf-8',code=200,headers=None):
        self.send_response(code);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)))
        for name,value in (headers or {}).items():self.send_header(name,value)
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(body)
    def do_GET(self):
        if self.headers.get('Host','').split(':')[0]not in('127.0.0.1','localhost'):self.send_body(b'Forbidden',code=403);return
        url=urlparse(self.path);query=parse_qs(url.query);run=query.get('run',[None])[0]
        if url.path=='/api/health':
            self.send_body(json.dumps({'service':'spireboard','api_version':1,'pid':os.getpid()}).encode());return
        if url.path=='/api/winning-route':
            path=repo.paths.get(run)
            if path is None:self.send_body(b'{"error":"Unknown run"}',code=404);return
            try:
                document=winning_routes.load(path)
                headers={}
                if query.get('export')==['1']:
                    payload=winning_routes.export(document)
                    headers['Content-Disposition']='attachment; filename="SpireBoard-winning-route.json"'
                elif 'step' in query:
                    payload=winning_routes.step(document,int(query['step'][0]))
                else:payload=winning_routes.metadata(document)
                self.send_body(json.dumps(payload,ensure_ascii=False).encode(),headers=headers)
            except (OSError,ValueError,KeyError,TypeError)as error:
                self.send_body(json.dumps({'error':str(error)},ensure_ascii=False).encode(),code=400)
            return
        if url.path in('/api/snapshot','/api/export'):
            self.send_body(json.dumps(repo.snapshot(run),ensure_ascii=False).encode());return
        if url.path=='/api/events':
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Cache-Control','no-cache');self.send_header('Connection','keep-alive');self.end_headers()
            q=broker.subscribe();last_file=0
            def send(event,data):self.wfile.write(('event: '+event+'\ndata: '+json.dumps(data,ensure_ascii=False)+'\n\n').encode());self.wfile.flush()
            try:
                send('snapshot',repo.snapshot(run))
                while True:
                    try:kind,data=q.get(timeout=15)
                    except queue.Empty:self.wfile.write(b': keepalive\n\n');self.wfile.flush();continue
                    if kind in('files','resync'):
                        # Native writes are atomic; notifications trigger re-read.
                        send('snapshot',repo.snapshot(run));last_file=time.monotonic()
                    elif kind=='telemetry'and(run is None or data['run']==run):send(kind,data)
                    elif kind=='watch_error':send(kind,data)
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
            finally:broker.remove(q)
            return
        target=STATIC/('index.html'if url.path=='/'else url.path.lstrip('/'))
        if not target.resolve().is_relative_to(STATIC.resolve())or not target.is_file():self.send_body(b'Not found',code=404);return
        self.send_body(target.read_bytes(),mimetypes.guess_type(target.name)[0]or'application/octet-stream')

    def do_POST(self):
        self.close_connection=True
        self.connection.settimeout(5)
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=4096:raise ValueError('Invalid body length')
            body=self.rfile.read(length)
        except (ValueError,OSError):
            self.send_body(b'{"error":"Invalid request body"}',code=400);return
        host=self.headers.get('Host','');origin=self.headers.get('Origin')
        if host.split(':')[0]not in('127.0.0.1','localhost')or(origin is not None and origin!='http://'+host):
            self.send_body(b'{"error":"Forbidden origin"}',code=403);return
        if not secrets.compare_digest(self.headers.get('X-SpireBoard-Token','').encode(),jobs.TOKEN.encode()):
            self.send_body(b'{"error":"Invalid local session token"}',code=403);return
        if urlparse(self.path).path!='/api/jobs':self.send_body(b'Not found',code=404);return
        try:
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('请求格式无效。')
            payload=json.loads(body)
            result=jobs.launch(payload,repo)
            broker.publish('files',{});self.send_body(json.dumps(result,ensure_ascii=False).encode(),code=202)
        except (ValueError,KeyError)as error:self.send_body(json.dumps({'error':str(error)},ensure_ascii=False).encode(),code=400)
        except Exception as error:self.send_body(json.dumps({'error':'无法启动任务：'+str(error)},ensure_ascii=False).encode(),code=500)

def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8765);a=p.parse_args()
    if os.name=='nt':
        # Keep dashboard sampling away from the A10 performance-core partition.
        k=c.WinDLL('kernel32');k.GetCurrentProcess.restype=w.HANDLE;k.SetProcessAffinityMask.argtypes=[w.HANDLE,c.c_size_t]
        k.SetProcessAffinityMask(k.GetCurrentProcess(),(1<<30)|(1<<31))
        roots=[ROOT/'experiments',ROOT/'dashboard']
        roots += [Path(x)for x in read(ROOT/'storage-policy.json',{}).get('artifact_roots',[])]
        for root in roots:threading.Thread(target=watch,args=(root,),daemon=True).start()
    threading.Thread(target=sample_loop,daemon=True).start()
    print(json.dumps({'url':f'http://127.0.0.1:{a.port}','mode':'local seed launcher','updates':'SSE + filesystem events; one-second OS telemetry'}),flush=True)
    ThreadingHTTPServer(('127.0.0.1',a.port),Handler).serve_forever()

if __name__=='__main__':main()
