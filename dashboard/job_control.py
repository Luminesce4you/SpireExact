"""Freeze and terminate only the named Windows Job owned by a manual run.

All processes in the job are suspended, including the outer runner, the solver
coordinator, its watchdog and newly discovered children. A paused run retains
live memory; this is not a durable snapshot that survives process termination.
"""
from __future__ import annotations
from contextlib import contextmanager
import ctypes as c
from ctypes import wintypes as w
import json, os, time, uuid
from pathlib import Path
from spire_exact.planning.io import write_json

SCHEMA = 'spire-manual-control/v1'
JOB_PREFIX = 'Local\\SpireExact.Manual.'
_owned_jobs = []  # Close only at process exit: KILL_ON_JOB_CLOSE includes us.
_elapsed_cache = {}


def _read(path, default=None):
    # Match the dashboard's rename-safe Windows sharing semantics.
    from dashboard.server import read
    return read(Path(path), default)


class _Windows:
    def __init__(self):
        if os.name != 'nt':
            raise RuntimeError('Manual process controls require Windows')
        self.k = c.WinDLL('kernel32', use_last_error=True)
        self.n = c.WinDLL('ntdll', use_last_error=True)
        signatures = {
            'CreateJobObjectW': ([c.c_void_p, w.LPCWSTR], w.HANDLE),
            'OpenJobObjectW': ([w.DWORD, w.BOOL, w.LPCWSTR], w.HANDLE),
            'AssignProcessToJobObject': ([w.HANDLE, w.HANDLE], w.BOOL),
            'SetInformationJobObject': ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL),
            'QueryInformationJobObject': ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p], w.BOOL),
            'TerminateJobObject': ([w.HANDLE, w.UINT], w.BOOL),
            'OpenProcess': ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            'GetCurrentProcess': ([], w.HANDLE),
            'CloseHandle': ([w.HANDLE], w.BOOL),
            'GetProcessTimes': ([w.HANDLE, c.c_void_p, c.c_void_p, c.c_void_p, c.c_void_p], w.BOOL),
            'GetExitCodeProcess': ([w.HANDLE, c.POINTER(w.DWORD)], w.BOOL),
            'IsProcessInJob': ([w.HANDLE, w.HANDLE, c.POINTER(w.BOOL)], w.BOOL),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.k, name); fn.argtypes = args; fn.restype = result
        for name in ('NtSuspendProcess', 'NtResumeProcess'):
            fn = getattr(self.n, name); fn.argtypes = [w.HANDLE]; fn.restype = c.c_long
        self.n.RtlNtStatusToDosError.argtypes = [c.c_long]
        self.n.RtlNtStatusToDosError.restype = w.ULONG

    def close(self, handle):
        self.k.CloseHandle(handle)

    def identity(self, handle, pid):
        code = w.DWORD()
        if not self.k.GetExitCodeProcess(handle, c.byref(code)) or code.value != 259:
            return None
        values = [c.c_ulonglong() for _ in range(4)]
        if not self.k.GetProcessTimes(handle, *(c.byref(v) for v in values)):
            raise c.WinError(c.get_last_error())
        return {'pid': int(pid), 'created_ticks': values[0].value}

    def pids(self, job):
        # Query may grow while children start; retry with the returned count.
        count = 32
        for _ in range(12):
            class List(c.Structure):
                _fields_ = [('assigned', w.DWORD), ('listed', w.DWORD),
                            ('pids', c.c_size_t * count)]
            row = List()
            if self.k.QueryInformationJobObject(job, 3, c.byref(row), c.sizeof(row), None):
                return [int(x) for x in row.pids[:row.listed]]
            if c.get_last_error() != 234:
                raise c.WinError(c.get_last_error())
            count = max(count * 2, int(row.assigned) + 16)
        raise RuntimeError('Job membership changed too quickly')

    @contextmanager
    def process(self, job, pid, expected=None):
        handle = self.k.OpenProcess(0x1000 | 0x0800, False, int(pid))
        if not handle:
            if c.get_last_error() in (87, 1168):
                yield None, None; return
            raise c.WinError(c.get_last_error())
        try:
            identity = self.identity(handle, pid)
            member = w.BOOL()
            if identity is None:
                yield None, None; return
            if expected is not None and identity != expected:
                raise RuntimeError('Process identity changed; refusing control')
            if not self.k.IsProcessInJob(handle, job, c.byref(member)):
                raise c.WinError(c.get_last_error())
            if not member.value:
                raise RuntimeError('Process is not in the owned job')
            yield handle, identity
        finally:
            self.close(handle)

    def transition(self, handle, suspend):
        result = getattr(self.n, 'NtSuspendProcess' if suspend else 'NtResumeProcess')(handle)
        if result < 0:
            raise c.WinError(self.n.RtlNtStatusToDosError(result))


@contextmanager
def _lock(folder):
    """Serialize HTTP controls across threads and dashboard restarts."""
    import msvcrt
    path = Path(folder) / 'control.lock'
    with path.open('a+b') as stream:
        if path.stat().st_size == 0:
            stream.write(b'0'); stream.flush()
        deadline = time.perf_counter() + 10
        while True:
            try:
                stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1); break
            except OSError:
                if time.perf_counter() >= deadline:
                    raise RuntimeError('Another process control is still in progress')
                time.sleep(.02)
        try:
            yield
        finally:
            stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def _event(folder, action, **fields):
    with (Path(folder) / 'control-events.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'event': action, 'epoch_seconds': time.time(), **fields}) + '\n')


def _publish(folder, control, phase):
    control['phase'] = phase
    control['updated_at'] = time.time()
    write_json(Path(folder) / 'control.json', control)


def _ledger(folder, state, *, begin=False, finish=False):
    path = Path(folder) / 'pause-ledger.json'
    value = _read(path, {'schema': 'spire-pause/v1', 'paused_total_seconds': 0.,
                         'paused_started_monotonic': None})
    now = time.perf_counter()
    if begin and value.get('paused_started_monotonic') is None:
        value['paused_started_monotonic'] = now
    if finish and value.get('paused_started_monotonic') is not None:
        value['paused_total_seconds'] += max(0., now - value['paused_started_monotonic'])
        value['paused_started_monotonic'] = None
    value['state'] = state
    write_json(path, value)
    return value


@contextmanager
def _job(folder):
    control = _read(Path(folder) / 'control.json', {})
    name = control.get('job_name', '')
    if control.get('schema') != SCHEMA or not name.startswith(JOB_PREFIX):
        raise RuntimeError('This run does not have a registered process control job')
    api = _Windows()
    handle = api.k.OpenJobObjectW(0x0004 | 0x0008, False, name)  # QUERY | TERMINATE
    if not handle:
        raise RuntimeError('The owned solver job has exited')
    try:
        identity = control.get('coordinator_identity', {})
        if not identity.get('pid'):
            raise RuntimeError('Missing coordinator identity')
        with api.process(handle, identity['pid'], identity) as (proc, actual):
            if proc is None:
                raise RuntimeError('The owned coordinator has exited')
        yield api, handle, control
    finally:
        api.close(handle)


def initialize(folder):
    """Called in the manual runner before waiting or creating any children."""
    folder = Path(folder)
    api = _Windows()
    name = JOB_PREFIX + uuid.uuid4().hex
    handle = api.k.CreateJobObjectW(None, name)
    if not handle:
        raise c.WinError(c.get_last_error())
    try:
        # Import definitions only; do not execute the limited runner.
        from tools.limited_cli import Extended
        limits = Extended(); limits.BasicLimitInformation.LimitFlags = 0x2000
        if not api.k.SetInformationJobObject(handle, 9, c.byref(limits), c.sizeof(limits)):
            raise c.WinError(c.get_last_error())
        proc = api.k.GetCurrentProcess()
        identity = api.identity(proc, os.getpid())
        if not api.k.AssignProcessToJobObject(handle, proc):
            raise c.WinError(c.get_last_error())
        _owned_jobs.append(handle)
        control = {'schema': SCHEMA, 'job_name': name, 'coordinator_identity': identity,
                   'phase': 'starting', 'suspended': [], 'started_counter': None,
                   'pause_scope': 'live_named_job_memory_only', 'durable_restart_supported': False}
        write_json(folder / 'control.json', control)
        _ledger(folder, 'running')
        write_json(folder / 'process.json', {'coordinator_pid': os.getpid(),
                                            'coordinator_identity': identity})
        _event(folder, 'control_registered', coordinator_identity=identity)
        return control
    except BaseException:
        # After assignment, never close a KILL_ON_JOB_CLOSE job containing us.
        if handle not in _owned_jobs:
            api.close(handle)
        raise


def mark_running(folder):
    with _lock(folder):
        value = _read(Path(folder) / 'control.json', {})
        if value.get('phase') == 'cancelled':
            return
        value['started_counter'] = time.perf_counter()
        value['pause_offset_at_start'] = _read(Path(folder) / 'pause-ledger.json', {}).get('paused_total_seconds', 0.)
        _publish(folder, value, 'running')


def mark_finished(folder, phase):
    with _lock(folder):
        value = _read(Path(folder) / 'control.json', {})
        if value.get('phase') == 'cancelled':
            return False
        value['finished_counter'] = time.perf_counter()
        _publish(folder, value, phase)
        return True


def pause(folder):
    folder = Path(folder)
    with _lock(folder), _job(folder) as (api, job, control):
        if control['phase'] == 'paused':
            return status(folder)
        if control['phase'] != 'running':
            raise ValueError('This run cannot currently be paused')
        control['previous_launch_phase'] = _read(folder / 'launch-state.json', {}).get('phase', 'running')
        control['suspended'] = []
        _ledger(folder, 'pausing', begin=True)
        _publish(folder, control, 'pausing')
        try:
            # Suspend the coordinator first to stop new task submission, then
            # suspend each descendant. Re-query until every live member is held.
            for _ in range(64):
                members = api.pids(job)
                known = {x['pid']: x for x in control['suspended']}
                pending = list(members)
                pending.sort(key=lambda pid: (pid != control['coordinator_identity']['pid'], pid))
                for pid in pending:
                    if pid == os.getpid():
                        raise RuntimeError('Controller cannot suspend itself')
                    with api.process(job, pid) as (handle, identity):
                        if handle is None:
                            continue
                        if known.get(pid) == identity:
                            continue
                        control['suspended'] = [x for x in control['suspended'] if x['pid'] != pid]
                        api.transition(handle, True)
                        control['suspended'].append(identity)
                        _publish(folder, control, 'pausing')
                if set(api.pids(job)) <= {x['pid'] for x in control['suspended']}:
                    break
            else:
                raise RuntimeError('Unable to freeze every owned job process')
            _ledger(folder, 'paused')
            _publish(folder, control, 'paused')
            write_json(folder / 'launch-state.json', {'phase': 'paused'})
            _event(folder, 'paused', process_count=len(control['suspended']))
        except BaseException:
            _ledger(folder, 'running', finish=True)
            for identity in reversed(control['suspended']):
                with api.process(job, identity['pid'], identity) as (handle, actual):
                    if handle is not None:
                        api.transition(handle, False)
            control['suspended'] = []
            _publish(folder, control, 'running')
            raise
    return status(folder)


def resume(folder):
    folder = Path(folder)
    with _lock(folder), _job(folder) as (api, job, control):
        if control['phase'] not in ('paused', 'pausing', 'resuming'):
            raise ValueError('This run is not paused')
        _ledger(folder, 'running', finish=True)
        _publish(folder, control, 'resuming')
        # Resume descendants before the outer runner; preserve creation identity
        # checks even though membership already excludes unrelated processes.
        while control['suspended']:
            identity = control['suspended'][-1]
            with api.process(job, identity['pid'], identity) as (handle, actual):
                if handle is not None:
                    api.transition(handle, False)
            control['suspended'].pop()
            _publish(folder, control, 'resuming')
        _publish(folder, control, 'running')
        write_json(folder / 'launch-state.json', {'phase': control.get('previous_launch_phase', 'running')})
        _event(folder, 'resumed')
    return status(folder)


def cancel(folder):
    folder = Path(folder)
    with _lock(folder), _job(folder) as (api, job, control):
        previous = dict(control)
        previous_ledger = _read(folder / 'pause-ledger.json', {})
        previous_launch = _read(folder / 'launch-state.json', {})
        _ledger(folder, 'cancelled', finish=True)
        control['finished_counter'] = time.perf_counter()
        _publish(folder, control, 'cancelled')
        write_json(folder / 'launch-state.json', {'phase': 'stopped', 'reason': 'user_cancelled'})
        _event(folder, 'cancelled', frozen=bool(control.get('suspended')))
        # Termination works while suspended and targets the complete named Job.
        if not api.k.TerminateJobObject(job, 130):
            error = c.WinError(c.get_last_error())
            # Never report a stopped run when Windows refused termination.
            write_json(folder / 'pause-ledger.json', previous_ledger)
            write_json(folder / 'launch-state.json', previous_launch)
            previous['last_control_error'] = str(error)
            _publish(folder, previous, previous.get('phase', 'running'))
            _event(folder, 'cancel_failed', error=str(error))
            raise error
    return status(folder)


def status(folder):
    folder = Path(folder)
    control = _read(folder / 'control.json', {})
    launch = _read(folder / 'launch-state.json', {})
    fallback = launch.get('phase', 'starting')
    result = {'phase': fallback, 'can_pause': False, 'can_resume': False,
              'can_stop': False, 'active_elapsed_seconds': 0.,
              'durable_restart_supported': False}
    if control.get('schema') != SCHEMA:
        # Preserve the last recorded duration for older manually cancelled runs.
        # Cache large result files by their publication identity.
        baseline = _read(folder / 'baseline-report.json', {})
        saved = baseline.get('wall_seconds')
        if not isinstance(saved, (int, float)):
            request = _read(folder / 'manual-request.json', {})
            seed = request.get('seed')
            path = folder / ('seed-' + seed) / 'result.json' if isinstance(seed, str) else None
            if path is not None:
                try:
                    stat = path.stat(); signature = (stat.st_mtime_ns, stat.st_size)
                    cached = _elapsed_cache.get(str(path))
                    if cached is None or cached[0] != signature:
                        saved = _read(path, {}).get('elapsed_seconds', 0.)
                        _elapsed_cache[str(path)] = (signature, saved)
                    else:
                        saved = cached[1]
                except OSError:
                    saved = 0.
        result['active_elapsed_seconds'] = max(0., float(saved or 0.))
        return result
    alive = False
    try:
        with _job(folder) as (api, job, current):
            alive = True
    except (OSError, RuntimeError):
        pass
    phase = control.get('phase', fallback)
    if phase == 'cancelled':
        phase = 'stopped'
    elif not alive and phase not in ('completed', 'error'):
        phase = fallback if fallback in ('completed', 'error', 'stopped') else 'stopped'
    elif phase == 'starting' and fallback == 'queued':
        phase = 'queued'
    started = control.get('started_counter')
    ledger = _read(folder / 'pause-ledger.json', {})
    paused = ledger.get('paused_total_seconds', 0.)
    end = control.get('finished_counter', time.perf_counter())
    if ledger.get('paused_started_monotonic') is not None:
        paused += max(0., end - ledger['paused_started_monotonic'])
    # Pauses during the initial queue do not consume solve time; the solve clock
    # begins after the queue. Capture its pause offset when mark_running executes.
    paused -= control.get('pause_offset_at_start', 0.)
    elapsed = 0. if started is None else max(0., end - started - paused)
    result.update(phase=phase, alive=alive, active_elapsed_seconds=elapsed,
                  can_pause=alive and phase == 'running',
                  can_resume=alive and phase in ('paused', 'pausing', 'resuming'),
                  can_stop=alive and phase not in ('stopped', 'completed', 'error'),
                  frozen_process_count=len(control.get('suspended', [])),
                  pause_scope=control.get('pause_scope'), sampled_at=time.time())
    return result
