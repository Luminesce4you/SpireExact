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
    seed=payload.get('seed');minutes=payload.get('minutes',180)
    if not isinstance(seed,str)or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',seed.strip()):
        raise ValueError('种子需为 1–64 位字母、数字、下划线或连字符，首位为字母或数字。')
    if type(minutes)is not int or not 1<=minutes<=180:raise ValueError('时间预算需为 1–180 分钟。')
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

def launch(payload,repo):
    request=validate(payload)
    with LOCK:
        waits=blockers(repo)
        if len(waits)>=8:raise ValueError('当前排队任务较多，请等待已有任务完成。')
        profile=read(ROOT/'dashboard/solver-profile.json',{})
        if request['minutes']>15 and not profile.get('long_run_ready'):
            raise ValueError('长时间运行配置仍在验证，当前可先使用 1–15 分钟。')
        workspace=(ROOT/profile['workspace']).resolve()
        if not workspace.is_relative_to((ROOT/'experiments').resolve())or not(workspace/'freeze.json').is_file():
            raise RuntimeError('已验证的求解版本不可用。')
        if profile.get('runtime_profile')not in ('legacy','workstation-2','server-one-heap'):raise RuntimeError('运行时配置无效。')
        run_id='manual-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]
        folder=manual_base()/run_id;folder.mkdir()
        from spire_exact.planning.io import write_json
        accepted=datetime.datetime.now().astimezone().isoformat()
        manifest={'run_id':run_id,'manual':True,'protocol':'A10-seed-v2','seed':request['seed'],'solver_seed':271828,
                  'timestamp':accepted,'accepted_at':accepted,'wall_cap_seconds':request['minutes']*60,
                  'workload_bytes':14*1024**3,'os_reserve_bytes':2*1024**3,'version':read(workspace/'freeze.json',{}).get('source_version'),
                  'settings':['--character','IRONCLAD','--ascension','10','--unlocks','all','--workers','7','--dop','1','--nodes','60000'],
                  'workspace':str(workspace),'runtime_profile':profile['runtime_profile'],'old_prefixes_loaded':False,
                  'memory_protocol_override':'User authorized 16 GiB total; 14 GiB workload plus 2 GiB reserve',
                  'panel':'USER_INTERACTIVE','not_a_holdout_result':True}
        write_json(folder/'validation-manifest.json',manifest)
        register_manual_seed(ROOT,run_id,request['seed'],manifest['version'])
        write_json(folder/'manual-request.json',{**request,'wait_for':waits,'workspace':str(workspace),'runtime_profile':profile['runtime_profile']})
        write_json(folder/'launch-state.json',{'phase':'queued'if waits else'starting'})
        python=str(Path(sys.executable).with_name('python.exe'))
        env=dict(os.environ,PYTHONIOENCODING='utf-8')
        env['PATH']=str(ROOT.parent/'.tools/dotnet')+os.pathsep+env.get('PATH','')
        with(folder/'coordinator.log').open('wb')as log:
            proc=subprocess.Popen([python,str(ROOT/'dashboard/manual_runner.py'),'--run',str(folder.resolve())],
                                  cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
        write_json(folder/'process.json',{'coordinator_pid':proc.pid,'coordinator_identity':process_identity(proc.pid)})
        repo.index()
        return {'run_id':run_id,'queued':bool(waits),'url':'/?run='+run_id}
