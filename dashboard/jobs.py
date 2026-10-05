"""Local, validated job submission. Commands are argv arrays, never shell input."""
from pathlib import Path
import ctypes as c,datetime,json,os,re,secrets,subprocess,sys,threading,time,uuid

ROOT=Path(__file__).resolve().parents[1]
TOKEN=secrets.token_urlsafe(32)
LOCK=threading.Lock()

def process_identity(pid):
    if not pid or os.name!='nt':return None
    k=c.WinDLL('kernel32',use_last_error=True)
    k.OpenProcess.argtypes=[c.c_ulong,c.c_int,c.c_ulong];k.OpenProcess.restype=c.c_void_p
    k.CloseHandle.argtypes=[c.c_void_p];k.GetExitCodeProcess.argtypes=[c.c_void_p,c.POINTER(c.c_ulong)]
    k.GetProcessTimes.argtypes=[c.c_void_p,c.c_void_p,c.c_void_p,c.c_void_p,c.c_void_p]
    handle=k.OpenProcess(0x1000,False,int(pid))
    if not handle:return None
    try:
        code=c.c_ulong();k.GetExitCodeProcess(handle,c.byref(code))
        if code.value!=259:return None
        created=c.c_ulonglong();ended=c.c_ulonglong();kernel=c.c_ulonglong();user=c.c_ulonglong()
        if not k.GetProcessTimes(handle,c.byref(created),c.byref(ended),c.byref(kernel),c.byref(user)):return None
        return {'pid':int(pid),'created_ticks':created.value}
    finally:k.CloseHandle(handle)

def validate(payload):
    if not isinstance(payload,dict)or set(payload)-{'seed','minutes'}:raise ValueError('请求只能包含种子和时间预算。')
    seed=payload.get('seed');minutes=payload.get('minutes',30)
    if not isinstance(seed,str)or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',seed.strip()):
        raise ValueError('种子需为 1–64 位字母、数字、下划线或连字符，首位为字母或数字。')
    if type(minutes)is not int or not 1<=minutes<=45:raise ValueError('时间预算需为 1–45 分钟。')
    return {'seed':seed.strip(),'minutes':minutes}

def read(path,default=None):
    # Import lazily to share rename-safe readers with the HTTP server.
    from dashboard.server import read as shared_read
    return shared_read(path,default)

def manual_base():
    policy=read(ROOT/'storage-policy.json',{})
    artifact=Path(policy['artifact_roots'][0]).resolve()
    target=artifact/'interactive';target.mkdir(parents=True,exist_ok=True)
    link=ROOT/'experiments/iteration-manual'
    if not link.exists():
        env=dict(os.environ,SPIRE_MANUAL_LINK=str(link),SPIRE_MANUAL_TARGET=str(target))
        subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',
            "$ErrorActionPreference='Stop'; New-Item -ItemType Junction -Path $env:SPIRE_MANUAL_LINK -Value $env:SPIRE_MANUAL_TARGET | Out-Null"],
            env=env,check=True,capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
    if not os.path.samefile(link,target):raise RuntimeError('手动任务目录已有其他内容，无法安全使用。')
    return link

def blockers(repo):
    candidates=[]
    from tools.cpu_topology import inventory,homogeneous_cpus
    desired=set(homogeneous_cpus(inventory(),8)[0])
    for path in list(repo.paths.values()):
        folder=path.parent
        if read(path,{}).get('manual'):
            meta=read(folder/'process.json',{})or{}
            item=meta.get('coordinator_identity')
            if item and process_identity(item['pid'])==item:candidates.append(item)
        for marker in folder.glob('*.job-active.json'):
            meta=read(marker,{})or{};item=process_identity(meta.get('pid'))
            if not item:continue
            # Reject stale marker files if Windows has reused their PID.
            created=item['created_ticks']/1e7-11644473600
            if not 0<=meta.get('time',0)-created<600:continue
            events=read_events(marker.with_name(marker.name.replace('.job-active.json','-events.jsonl')))
            cpus=next((e.get('cpus',[])for e in events if e.get('event')=='started'),[])
            if not cpus or desired.intersection(cpus):candidates.append(item)
    for marker in (ROOT/'dashboard/runtime/reservations').glob('*.json'):
        item=read(marker,{})or{}
        identity={k:item.get(k)for k in ('pid','created_ticks')}
        if identity['pid']and process_identity(identity['pid'])==identity:candidates.append(identity)
    return list({(r['pid'],r['created_ticks']):r for r in candidates}.values())

def read_events(path):
    from dashboard.server import json_lines
    return json_lines(path)

def register_manual_seed(root,run_id,seed,version):
    from tools.seed_registry import locked
    from spire_exact.planning.io import read_json,write_json
    ledger=Path(root)/'experiments/seed-ledger.json'
    with locked(ledger.with_suffix('.lock')):
        data=read_json(ledger)
        if not any(r['run_id']==run_id for r in data['runs']):
            data['runs'].append({'run_id':run_id,'version':version,'kind':'user_interactive','seeds':[seed],
                                 'not_a_holdout_result':True})
            write_json(ledger,data)

def solver_settings(request,profile):
    """Explicit, reviewable settings of the manual entry, written before launch."""
    seconds=request['minutes']*60
    extra=profile.get('solver_settings',[])
    if not isinstance(extra,list) or any(not isinstance(v,str)for v in extra):
        raise ValueError('求解入口参数无效。')
    protected={'--seed','--out','--game-dir','--character','--ascension','--unlocks','--workers','--dop',
               '--seconds','--prefix','--import-checkpoints','--scope-prefix'}
    if any(v in protected for v in extra):raise ValueError('入口不能覆盖种子、初始状态或作业边界。')
    return ['--seed',request['seed'],'--character','IRONCLAD','--ascension','10','--unlocks','all','--workers','7','--dop','1',
        '--worker-memory-mib',str(profile.get('worker_memory_mib',1792)),'--reserve-mib','1024',
        '--evaluations','1000000','--seconds',str(max(10,seconds-30)),
        '--task-seconds',str(min(1200,seconds)),'--max-decisions','12000','--lookahead-actions','12000',
        '--lookahead-floors','99','--alternatives','8','--survivors','2','--budget-ms','600000',
        '--boss-budget-ms','600000','--nodes','60000','--profile','Low',
        '--dispatch','ordered','--dispatch-window','56','--solver-seed','271828','--low-io','--event-driven-settle',
        '--checkpoint-mib','1024','--cache-mib','128','--archive-entries','256',
        '--runtime-profile',profile['runtime_profile'],*extra]

def pause_compatible(folder):
    manifest=read(Path(folder)/'validation-manifest.json',{})or{}
    if not isinstance(manifest,dict):return False
    workspace=manifest.get('workspace')
    freeze=read(Path(workspace)/'freeze.json',{})if workspace else {}
    if manifest.get('pause_excluded_budget_clock')is not True:return False
    capabilities=freeze.get('capabilities')if isinstance(freeze,dict)else None
    if isinstance(capabilities,dict)and capabilities.get('interactive_pause_clock')=='evaluate-native-and-python/v1':
        return True
    validation=manifest.get('pause_clock_validation')
    if not isinstance(validation,dict):return False
    path=validation.get('report_path')
    if not isinstance(path,str)or not path:return False
    path=Path(path);path=path if path.is_absolute()else ROOT/path
    report=read(path,{})
    if not isinstance(report,dict):return False
    clock=report.get('pause_clock')
    settings=manifest.get('settings')
    if not isinstance(clock,dict)or not isinstance(settings,list)or any(not isinstance(x,str)for x in settings):return False
    gate='none'
    for i,flag in enumerate(settings):
        if flag=='--gate-preset':
            if i+1>=len(settings):return False
            gate=settings[i+1]
    return (gate in ('none','escalate-evaluate') and report.get('passed')is True
            and report.get('returncode')==0 and type(report.get('actions_observed'))is int and report['actions_observed']==0
            and report.get('full_search_started')is False and clock.get('enabled')is True
            and type(clock.get('patched_methods'))is int and clock['patched_methods']>0
            and isinstance(manifest.get('version'),str) and bool(manifest['version'])
            and validation.get('source_version')==manifest['version']==report.get('source_version')
            and isinstance(manifest.get('host_sha256'),str) and bool(manifest['host_sha256'])
            and validation.get('host_sha256')==manifest['host_sha256']==report.get('host_sha256'))


def registered_control(folder,manifest=None):
    """Validate file binding before the existing Job/identity OS validation."""
    from dashboard import job_control
    folder=Path(folder)
    manifest=read(folder/'validation-manifest.json',{})if manifest is None else manifest
    unavailable='此任务未接入受验证的作业控制，无法暂停或退出。'
    if not isinstance(manifest,dict):return False,unavailable
    manual=manifest.get('manual')is True
    schema=manifest.get('process_control_schema')
    if schema!=job_control.SCHEMA and not(manual and schema is None):return False,unavailable
    control=read(folder/'control.json',{});process=read(folder/'process.json',{})
    if not isinstance(control,dict)or not isinstance(process,dict):return False,'作业控制记录缺失或无效，已禁用操作。'
    name=control.get('job_name')
    if(control.get('schema')!=job_control.SCHEMA or not isinstance(name,str)
            or not re.fullmatch(re.escape(job_control.JOB_PREFIX)+r'[0-9a-f]{32}',name)):
        return False,'作业控制记录缺失或无效，已禁用操作。'
    identity=control.get('coordinator_identity')
    if (not isinstance(identity,dict)or set(identity)!={'pid','created_ticks'}
            or any(type(identity[k])is not int or identity[k]<1 for k in ('pid','created_ticks'))
            or process.get('coordinator_identity')!=identity
            or type(process.get('coordinator_pid'))is not int or process['coordinator_pid']!=identity['pid']):
        return False,'作业控制身份与运行记录不一致，已禁用操作。'
    if not manual or 'run_id'in control or 'run_folder'in control:
        run_id=manifest.get('run_id');run_folder=control.get('run_folder')
        if(not isinstance(run_id,str)or not run_id or control.get('run_id')!=run_id
                or not isinstance(run_folder,str)or not run_folder):
            return False,'作业控制记录不属于这次求解，已禁用操作。'
        try:
            if Path(run_folder).resolve()!=folder.resolve():return False,'作业控制目录与这次求解不一致，已禁用操作。'
        except (OSError,ValueError):return False,'作业控制目录无效，已禁用操作。'
    return True,None


def control_status(folder,fallback_elapsed_seconds=None):
    from dashboard import job_control
    registered,reason=registered_control(folder)
    if not registered:
        return {'phase':'unavailable','registered':False,'can_pause':False,'can_resume':False,'can_stop':False,
                'control_unavailable_reason':reason,'sampled_at':time.time()}
    try:result=job_control.status(folder)
    except (OSError,RuntimeError,ValueError,TypeError,AttributeError):
        return {'phase':'unavailable','registered':True,'can_pause':False,'can_resume':False,'can_stop':False,
                'control_unavailable_reason':'无法验证具名作业身份，已禁用操作。','sampled_at':time.time()}
    phase=result.get('phase',result.get('state','error'))
    if phase in ('cancelled','canceled'):phase='stopped'
    state={**result,'phase':phase,'registered':True,'elapsed_seconds':result.get('active_elapsed_seconds',result.get('elapsed_seconds',0)),
           'sampled_at':time.time()}
    control=read(Path(folder)/'control.json',{})or{}
    if not isinstance(control,dict):control={}
    state['user_cancelled']=control.get('phase')in('cancelled','canceled')
    if not state.get('alive'):
        state.update(can_pause=False,can_resume=False,can_stop=False,control_unavailable_reason='作业已结束，已有记录仍可查看。')
        if 'finished_counter'not in control:
            if isinstance(fallback_elapsed_seconds,(int,float))and not isinstance(fallback_elapsed_seconds,bool):
                state['elapsed_seconds']=max(0,fallback_elapsed_seconds)
            else:state.pop('elapsed_seconds',None)
            if not state['user_cancelled']:state['control_unavailable_reason']='作业进程已停止，尚无最终结束报告；保留最后保存的状态。'
    if not pause_compatible(folder):
        state.update(can_pause=False,can_resume=False,pause_unavailable_reason='此任务没有通过暂停计时兼容验证，仅支持退出。')
    return state

def control(run_id,action,payload,repo):
    if payload!={}:raise ValueError('求解控制请求无需附加参数。')
    if action not in ('pause','resume','stop'):raise ValueError('未知求解控制。')
    path=repo.paths.get(run_id)
    if path is None:raise KeyError('未找到这次求解。')
    manifest=read(path,{})
    if not isinstance(manifest,dict)or manifest.get('run_id')!=run_id:raise ValueError('运行身份与求解记录不一致。')
    registered,reason=registered_control(path.parent,manifest)
    if not registered:raise ValueError(reason)
    from dashboard import job_control
    with LOCK:
        if action in ('pause','resume')and not pause_compatible(path.parent):
            raise ValueError('此任务使用旧宿主，暂停计时不兼容；可以退出并使用新入口重新求解。')
        before=control_status(path.parent)
        if not before.get({'pause':'can_pause','resume':'can_resume','stop':'can_stop'}[action]):
            raise ValueError(before.get('control_unavailable_reason')or before.get('pause_unavailable_reason')or'这次求解当前无法执行此操作。')
        result=getattr(job_control,{'pause':'pause','resume':'resume','stop':'cancel'}[action])(path.parent)
        state=control_status(path.parent)
        expected={'pause':'paused','resume':'running','stop':'stopped'}[action]
        if state['phase']!=expected:raise ValueError('求解状态已改变，请刷新后查看。')
    return {'run_id':run_id,'job_control':state}

def launch(payload,repo):
    request=validate(payload)
    with LOCK:
        waits=blockers(repo)
        if len(waits)>=8:raise ValueError('当前排队任务较多，请等待已有任务完成。')
        profile=read(ROOT/'dashboard/solver-profile.json',{})
        from tools.prepare_dashboard import PUBLIC_PROFILE, PUBLIC_RELEASE, validate_ready
        prepared=validate_ready(profile)
        if request['minutes']>15 and not profile.get('long_run_ready'):
            raise ValueError('长时间运行配置仍在验证，当前可先使用 1–15 分钟。')
        workspace=(ROOT/profile['workspace']).resolve()
        if workspace!=ROOT.resolve() or profile.get('public_source') is not True:
            raise RuntimeError('已验证的求解版本不可用。')
        from spire_exact.planning.pool import RUNTIME_PROFILES
        if profile.get('runtime_profile')not in RUNTIME_PROFILES:raise RuntimeError('运行时配置无效。')
        run_id='manual-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]
        folder=manual_base()/run_id;folder.mkdir()
        from spire_exact.planning.io import write_json
        accepted=datetime.datetime.now().astimezone().isoformat()
        manifest={'run_id':run_id,'manual':True,'protocol':'A10-seed-v2','seed':request['seed'],'solver_seed':271828,
                  'timestamp':accepted,'accepted_at':accepted,'wall_cap_seconds':request['minutes']*60,
                  'workload_bytes':14*1024**3,'os_reserve_bytes':2*1024**3,'version':prepared['source_version'],
                  'host_sha256':prepared['host_sha256'],'feature_profile':PUBLIC_PROFILE,
                  'source_release':PUBLIC_RELEASE,
                  'pause_clock_validation':{'report_path':prepared['report_path'],
                      'source_version':prepared['source_version'],'host_sha256':prepared['host_sha256']},
                  'settings':solver_settings(request,profile),
                  'workspace':str(workspace),'runtime_profile':profile['runtime_profile'],'old_prefixes_loaded':False,
                  'memory_protocol_override':'User authorized 16 GiB total; 14 GiB workload plus 2 GiB reserve',
                  'panel':'USER_INTERACTIVE','not_a_holdout_result':True}
        write_json(folder/'validation-manifest.json',manifest)
        register_manual_seed(ROOT,run_id,request['seed'],manifest['version'])
        write_json(folder/'manual-request.json',{**request,'wait_for':waits,'workspace':str(workspace),
            'runtime_profile':profile['runtime_profile'],'settings':manifest['settings']})
        write_json(folder/'launch-state.json',{'phase':'queued'if waits else'starting'})
        python=str(Path(sys.executable).with_name('python.exe'))
        from tools.run_seed import spawn_detached
        # The manual runner registers its own Python PID/creation identity and
        # named Job. The detached wrapper is intentionally not the final owner.
        pid=spawn_detached([python,str(ROOT/'dashboard/manual_runner.py'),'--run',str(folder.resolve())],
                           folder/'coordinator.log',ROOT)
        if not (folder/'process.json').exists():
            write_json(folder/'process.json',{'launcher_pid':pid,'coordinator_pid':pid,
                'coordinator_identity':process_identity(pid),'owner_pending':True})
        repo.index()
        return {'run_id':run_id,'queued':bool(waits),'url':'/?run='+run_id}
