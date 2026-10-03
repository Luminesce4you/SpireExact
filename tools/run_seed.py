"""Run one fresh A10 seed inside the standard Job, from the main tree or a frozen workspace.

Same resource enforcement as dashboard/manual_runner.py (tools/limited_cli.py: 8 P-core logical CPUs,
14 GiB Job + 2 GiB reserve, wall cap). Output goes to <artifact root>/<iteration>/<name>, reachable for
SpireBoard through the junction experiments/<iteration>-runs. Nothing here claims a win: only a
certificate written by the solver after an independent fresh replay counts.

Start long runs with --detach. A process started from an agent's shell (directly or with
Start-Process) stays inside that application's Windows Job and process tree and dies with it:
i039-infra-stability-01 stopped at 22:13 on 2026-10-01, 102 minutes in, when the Codex app restarted.
--detach re-launches this command through WMI, which places it in the logon session's job instead.

    python tools/run_seed.py --seed 10101010 --minutes 180 --iteration iteration-037 --name focus-180-a \
        --workspace experiments/frozen-i038 --detach
"""
from pathlib import Path
import argparse, ctypes as c, datetime, json, os, subprocess, sys, time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from spire_exact.planning.io import read_json, write_json
from tools.rolling_storage import owner_root

MAIN = owner_root(ROOT)

PROFILES = {
    # The retained frozen-i035 configuration, for like-for-like reruns.
    'baseline': ['--scheduler', 'weighted', '--runtime-profile', 'server-one-heap', '--worker-memory-mib', '1536'],
    'focus': ['--scheduler', 'focus', '--prior', '--gate-preset', 'escalate', '--repair-mode', 'gate',
              '--normal-nodes', '10000', '--runtime-profile', 'server-large-gen0', '--worker-memory-mib', '1792'],
}


def register(run_id, seed, version, panel='USER_INTERACTIVE'):
    from tools.seed_registry import locked
    ledger = MAIN / 'experiments/seed-ledger.json'
    with locked(ledger.with_suffix('.lock')):
        data = read_json(ledger)
        if not any(r['run_id'] == run_id for r in data['runs']):
            data['runs'].append({'run_id': run_id, 'version': version, 'kind': 'user_interactive' if panel == 'USER_INTERACTIVE' else panel + '_run',
                                 'seeds': [seed], 'not_a_holdout_result': True})
            write_json(ledger, data)


def run_base(iteration):
    """<artifact root>/<iteration>, with the junction SpireBoard indexes through."""
    artifact = Path(read_json(MAIN / 'storage-policy.json')['artifact_roots'][0]).resolve()
    target = artifact / iteration
    target.mkdir(parents=True, exist_ok=True)
    link = MAIN / 'experiments' / (iteration + '-runs')
    if not link.exists():
        env = dict(os.environ, SPIRE_RUN_LINK=str(link), SPIRE_RUN_TARGET=str(target))
        subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                        "$ErrorActionPreference='Stop'; New-Item -ItemType Junction -Path $env:SPIRE_RUN_LINK -Value $env:SPIRE_RUN_TARGET | Out-Null"],
                       env=env, check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    if not os.path.samefile(link, target):
        raise SystemExit('run directory junction points elsewhere: ' + str(link))
    return target


def spawn_detached(command, log, cwd):
    """Start `command` outside the caller's Windows Job and process tree; returns its process id.

    Win32_Process.Create makes the WMI provider host the parent, so closing or restarting the
    application that ran this launcher does not take the run down. Output goes to `log`."""
    line = 'cmd.exe /d /s /c "' + subprocess.list2cmdline([str(x) for x in command]) + ' > "' + str(log) + '" 2>&1"'
    env = dict(os.environ, SPIRE_DETACH_COMMAND=line, SPIRE_DETACH_CWD=str(cwd))
    done = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                           "$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments "
                           "@{CommandLine=$env:SPIRE_DETACH_COMMAND; CurrentDirectory=$env:SPIRE_DETACH_CWD}; "
                           "if ($r.ReturnValue -ne 0) { exit 1 }; $r.ProcessId"],
                          env=env, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    if done.returncode:
        raise SystemExit('detached start failed: ' + (done.stderr or done.stdout).strip())
    return int(done.stdout.strip().splitlines()[-1])


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--seed', required=True); p.add_argument('--minutes', type=int, required=True)
    p.add_argument('--iteration', required=True, help='experiment folder name, e.g. iteration-037')
    p.add_argument('--name', required=True); p.add_argument('--profile', choices=sorted(PROFILES), default='focus')
    p.add_argument('--workspace', type=Path, default=MAIN, help='main tree (default) or an experiments/frozen-* snapshot')
    p.add_argument('--solver-seed', type=int, default=271828); p.add_argument('--window', type=int, default=56)
    p.add_argument('--logical-cpus', type=int, choices=[8, 16], default=8,
                   help='8 spreads across physical cores; 16 also uses SMT siblings in the same CPU class')
    p.add_argument('--workers', type=int, default=7)
    p.add_argument('--job-memory-mib', type=int, default=14336,
                   help='Enforced Job cap; nondefault values require user authorization recorded in --resource-note')
    p.add_argument('--resource-note', default='', help='Reason and authorization for a resource protocol override')
    p.add_argument('--efficiency-class', type=int, default=None,
                   help='CPU set class for the job (cpu_topology inventory); default = the fastest class with 8 free logical CPUs. '
                        'On the i9-13900HX 1 = P cores, 0 = E cores; never compare wall time across classes')
    p.add_argument('--panel', choices=['USER_INTERACTIVE', 'DEV', 'TRAIN', 'HARD'], default='USER_INTERACTIVE',
                   help='panel the seed belongs to (manifest and seed ledger); HOLDOUT runs are not started from here')
    p.add_argument('--detach', action='store_true', help='re-launch outside the calling application and return at once')
    p.add_argument('--extra', nargs=argparse.REMAINDER, default=[], help='extra solve-p5 arguments appended last')
    a = p.parse_args()
    if not 1 <= a.minutes <= 180:
        raise SystemExit('minutes must be within the 180 min protocol cap')
    if not 1 <= a.workers <= a.logical_cpus or not 1024 <= a.job_memory_mib <= 28672:
        raise SystemExit('workers must fit CPUs; Job memory must be within 1024..28672 MiB')
    if (a.logical_cpus != 8 or a.workers != 7 or a.job_memory_mib != 14336) and not a.resource_note.strip():
        raise SystemExit('resource overrides need an explicit --resource-note')
    protocol = 'A10-seed-v2-scaling16' if a.logical_cpus == 16 else 'A10-seed-v2'
    workspace = (a.workspace if a.workspace.is_absolute() else MAIN / a.workspace).resolve()
    frozen = workspace != MAIN
    if frozen and (not workspace.is_relative_to((MAIN / 'experiments').resolve()) or not (workspace / 'freeze.json').is_file()):
        raise SystemExit('workspace must be the main tree or a frozen snapshot with freeze.json')
    folder = run_base(a.iteration) / a.name
    if folder.exists():
        raise SystemExit('run name already exists; never reuse an output directory')
    if a.detach:
        own = sys.argv[1:]
        cut = own.index('--extra') if '--extra' in own else len(own)
        argv = [x for x in own[:cut] if x != '--detach'] + own[cut:]
        log = folder.parent / (a.name + '.launcher.log')
        pid = spawn_detached([sys.executable, Path(__file__).resolve(), *argv], log, MAIN)
        print(json.dumps({'detached_pid': pid, 'run': str(folder), 'launcher_log': str(log)}))
        return
    # Same convention as dashboard/jobs.py: the workspace .NET 9 SDK must precede any system dotnet.
    sdk = MAIN.parent / '.tools/dotnet'
    if not (sdk / 'dotnet.exe').is_file():
        raise SystemExit('workspace .NET SDK not found: ' + str(sdk))
    os.environ['PATH'] = str(sdk) + os.pathsep + os.environ.get('PATH', '')
    os.environ['DOTNET_ROOT'] = str(sdk)
    folder.mkdir(parents=True)
    from tools.cpu_topology import inventory, homogeneous_cpus
    from tools.experiment import version_hash
    from tools.experiment_storage import prepare_storage
    from tools.rolling_storage import admission, active_seed, RollingConsole, logical_bytes
    cpus, cls = homogeneous_cpus(inventory(), a.logical_cpus, a.efficiency_class)
    k = c.WinDLL('kernel32', use_last_error=True); k.GetCurrentProcess.restype = c.c_void_p
    k.SetProcessAffinityMask.argtypes = [c.c_void_p, c.c_size_t]
    if not k.SetProcessAffinityMask(k.GetCurrentProcess(), sum(1 << i for i in cpus)):
        raise c.WinError(c.get_last_error())
    admission(MAIN); prepare_storage(folder)
    data = workspace / 'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
    # Compile the workspace's own host before the timed job starts.
    built = subprocess.run([sys.executable, '-c',
                            'import sys;from pathlib import Path;from spire_exact.native import build_host;'
                            'print(build_host(Path(sys.argv[1]))[2]["host_sha256"])', str(data)],
                           cwd=workspace, capture_output=True, text=True)
    if built.returncode:
        raise SystemExit('native host build failed:\n' + built.stdout + built.stderr)
    host = built.stdout.strip().splitlines()[-1]
    version = read_json(workspace / 'freeze.json')['source_version'] if frozen else version_hash()
    seconds = a.minutes * 60
    settings = ['--seed', a.seed, '--character', 'IRONCLAD', '--ascension', '10', '--unlocks', 'all', '--workers', str(a.workers), '--dop', '1',
                '--reserve-mib', '1024', '--evaluations', '1000000', '--seconds', str(max(10, seconds - 30)),
                '--task-seconds', str(min(1200, seconds)), '--max-decisions', '12000', '--lookahead-actions', '12000',
                '--lookahead-floors', '99', '--alternatives', '8', '--survivors', '2', '--budget-ms', '600000',
                '--boss-budget-ms', '600000', '--nodes', '60000', '--profile', 'Low', '--dispatch', 'ordered',
                '--dispatch-window', str(a.window), '--solver-seed', str(a.solver_seed), '--low-io', '--event-driven-settle',
                '--checkpoint-mib', '1024', '--cache-mib', '128', '--archive-entries', '256', *PROFILES[a.profile], *a.extra]
    manifest = {'run_id': a.name, 'protocol': protocol, 'seed': a.seed, 'solver_seed': a.solver_seed, 'profile': a.profile,
                'timestamp': datetime.datetime.now().astimezone().isoformat(), 'wall_cap_seconds': seconds,
                'workload_bytes': a.job_memory_mib * 1024 ** 2, 'os_reserve_bytes': 2 * 1024 ** 3,
                'memory_protocol_override': a.resource_note or 'User authorized 16 GiB total; 14 GiB workload plus 2 GiB reserve',
                'version': version, 'host_sha256': host, 'workspace': str(workspace), 'workspace_frozen': frozen,
                'settings': settings, 'cpu_set': cpus, 'efficiency_class': cls, 'old_prefixes_loaded': False,
                'panel': a.panel, 'not_a_holdout_result': True}
    if a.resource_note:
        manifest['resource_protocol_override'] = {'logical_cpus': a.logical_cpus, 'requested_workers': a.workers,
                                                'job_memory_mib': a.job_memory_mib, 'authorization': a.resource_note}
    write_json(folder / 'validation-manifest.json', manifest)
    register(a.name, a.seed, version, a.panel)
    target = folder / ('seed-' + a.seed)
    cmd = [sys.executable, str(workspace / 'tools/limited_cli.py'), 'solve-p5', '--out', str(target), '--game-dir', str(data), *settings]
    env = dict(os.environ, SPIRE_PROTOCOL=protocol, SPIRE_WALL_LIMIT_SECONDS=str(seconds), PYTHONIOENCODING='utf-8')
    if a.resource_note:
        env.update(SPIRE_JOB_MEMORY_MIB=str(a.job_memory_mib), SPIRE_RESOURCE_OVERRIDE_NOTE=a.resource_note,
                   SPIRE_REQUIRE_EXACT_WORKERS='1')
    started = time.perf_counter(); furthest = -1

    def event(kind, **values):
        with (folder / 'events.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps({'event': kind, 'wall_seconds': time.perf_counter() - started, **values}) + '\n')

    log = RollingConsole(folder / 'console.log')
    try:
        with active_seed(target), subprocess.Popen(cmd, cwd=workspace, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                                   creationflags=subprocess.CREATE_NO_WINDOW) as proc:
            write_json(folder / 'process.json', {'coordinator_pid': os.getpid(), 'owned_job_pid': proc.pid})
            event('baseline_started', seed=a.seed, protocol=protocol, cpu_set=cpus)
            for line in iter(proc.stdout.readline, b''):
                log.write(line)
                try: row = json.loads(line)
                except (ValueError, UnicodeDecodeError): continue
                floor = row.get('floor') if isinstance(row, dict) else None
                if isinstance(floor, int) and floor > furthest:
                    furthest = floor
                    event('new_furthest_evaluation', floor=floor, label=row.get('label'), classification=row.get('classification'))
            code = proc.wait()
    finally:
        log.close()
    result = read_json(target / 'result.json') if (target / 'result.json').exists() else {}
    resource_path = folder / (target.name + '-resources.json')
    resources = read_json(resource_path) if resource_path.exists() else {}
    win = code == 0 and result.get('status') == 'VERIFIED_WIN_IN_NATIVE_HOST' and (target / 'certificate.json').exists()
    report = {'protocol': protocol, 'seed': a.seed, 'profile': a.profile, 'verified_win': win, 'censored': not win,
              'exit_code': code, 'wall_seconds': time.perf_counter() - started, 'resources': resources,
              'file_bytes': logical_bytes(folder), 'normal_godot_verified': False, 'not_a_seed_infeasibility_proof': True,
              'user_interactive': True}
    # SpireBoard reads this name to tell a finished run from a live one.
    write_json(folder / 'baseline-report.json', report)
    event('baseline_completed' if code in (0, 124) else 'baseline_failed', verified_win=win, exit_code=code)
    print(json.dumps({k: report[k] for k in ('seed', 'profile', 'verified_win', 'exit_code', 'wall_seconds')}))


if __name__ == '__main__':
    main()
