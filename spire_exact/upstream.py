"""Local-only diagnostics plus explicitly requested source fetch/build/baseline tools.

Fetching code does not execute it. Native baseline is still upstream BEAM SEARCH.
Build uses CopyModOnBuild=false; no installation into the user's real game mods.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any
from .canonical import ContractError, read_json, write_json

ROOT = Path(__file__).resolve().parents[1]
PIN = '4d2c55069f2cf9f8c621631594857b412cf9660f'
REPOSITORY = 'https://github.com/Torch1230/CombatSolver.git'


def file_hash(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def run(command: list[str], cwd: Path | None = None, timeout: int = 120) -> str:
    try:
        completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                                   encoding='utf-8', errors='replace', timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ContractError(f'command failed: {command[0]}: {error}') from error
    if completed.returncode:
        raise ContractError(f'command returned {completed.returncode}: {command}\n{completed.stderr[-8000:]}\n{completed.stdout[-8000:]}')
    return completed.stdout.strip()


def verify_upstream(path: Path, allow_dirty: bool = False) -> dict[str, Any]:
    if not (path / '.git').is_dir():
        raise ContractError('not a fetched upstream Git worktree: ' + str(path))
    if (path / '.git/HEAD').is_file() and (path / '.git/objects').is_dir():
        (path / '.git/refs').mkdir(exist_ok=True)
    commit = run(['git', 'rev-parse', 'HEAD'], path)
    if commit != PIN:
        raise ContractError(f'upstream commit mismatch: {commit}, required {PIN}')
    dirty = run(['git', 'status', '--porcelain'], path)
    if dirty and not allow_dirty:
        raise ContractError('upstream has local changes; refuse to overwrite or certify it')
    files = ['CombatSolver.csproj', 'LICENSE', 'THIRD_PARTY_NOTICES.md',
             'src/Runtime/CombatRootSnapshot.cs', 'src/Search/CombatBeamSolver.Expansion.cs',
             'tools/OfflineSearchHarness/Program.cs']
    fingerprints = {}
    for relative in files:
        item = path / relative
        if not item.is_file():
            raise ContractError('missing pinned-source file: ' + relative)
        fingerprints[relative] = file_hash(item)
    source_hash = hashlib.sha256()
    names = run(['git','ls-files','--cached','--others','--exclude-standard','-z'], path).split('\0')
    # Include local build configuration by hash, not its potentially private contents.
    names += [name for name in ('local.props',) if (path/name).is_file()]
    source_count = 0
    for name in sorted(set(names)):
        item = path / name
        if not name or item.suffix not in ('.cs','.csproj','.props','.targets','.json'):
            continue
        value = file_hash(item) if item.is_file() else 'MISSING'
        source_hash.update((name+'\0'+value+'\n').encode('utf-8'))
        source_count += 1
    return {'repository': REPOSITORY, 'commit': commit, 'dirty': bool(dirty),
            'dirty_entries': dirty.splitlines(), 'selected_source_sha256': fingerprints,
            'source_and_config_tree_sha256': source_hash.hexdigest(),
            'source_and_config_files_hashed': source_count}


def fetch_upstream(destination: Path) -> dict[str, Any]:
    destination = destination.resolve()
    if destination.exists():
        return verify_upstream(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + '.fetching')
    if temporary.exists():
        raise ContractError('temporary checkout already exists: ' + str(temporary))
    try:
        temporary.mkdir()
        run(['git', 'init', '-q'], temporary)
        run(['git', 'remote', 'add', 'origin', REPOSITORY], temporary)
        run(['git', 'fetch', '--depth', '1', 'origin', PIN], temporary, timeout=300)
        run(['git', 'checkout', '--detach', 'FETCH_HEAD'], temporary)
        result = verify_upstream(temporary)
        temporary.rename(destination)
        return result
    except Exception:
        if temporary.exists(): shutil.rmtree(temporary)
        raise


def audit_source(path: Path) -> dict[str, Any]:
    provenance = verify_upstream(path, allow_dirty=True)
    patterns = {
        'beam_or_top_k': r'\b(?:BeamWidth|Take|SelectActionCandidates|TopQueueActionsDropped)\b',
        'choice_limit': r'\b(?:MaxCardBranchesPerNode|MaxPileChoiceBranchesPerAction|MaxHandChoiceBranchesPerAction)\b',
        'cycle_or_progress': r'\b(?:ShouldRejectCycleCandidate|ShouldPruneCrossTurnNoProgress|CycleProbeLease)\b',
        'policy_filter': r'\b(?:GrowthCostPolicy|AllowsPotionUse|CanStopAtHpTarget)\b',
    }
    evidence = []
    for folder in ('src/Search', 'src/Runtime'):
        for file in sorted((path / folder).rglob('*.cs')):
            for line_number, line in enumerate(file.read_text(encoding='utf-8-sig').splitlines(), 1):
                for category, pattern in patterns.items():
                    if re.search(pattern, line):
                        evidence.append({'category': category, 'path': file.relative_to(path).as_posix(),
                                         'line': line_number, 'text': line.strip()[:500]})
    return {'provenance': provenance, 'heuristic_audit_candidates': evidence,
            'scope': 'static review index, NOT a complete semantic audit',
            'native_adapter_implemented': False}


def doctor(game_dir: Path | None = None, ritsu_dir: Path | None = None,
           upstream: Path | None = None, mods_dir: Path | None = None) -> dict[str, Any]:
    checks: dict[str, Any] = {'python': os.sys.version.split()[0], 'git': shutil.which('git'),
                              'dotnet': shutil.which('dotnet')}
    issues = []
    if checks['dotnet']:
        try:
            checks['dotnet_sdks'] = run([checks['dotnet'], '--list-sdks'], timeout=20).splitlines()
            if not checks['dotnet_sdks']:
                issues.append('missing .NET SDK; dotnet host is installed but no SDK is available')
        except ContractError as error: issues.append(str(error))
    else:
        issues.append('missing .NET SDK; native CombatSolver build not run')
    data = None
    if game_dir:
        candidates = [game_dir, game_dir / 'data_sts2_windows_x86_64',
                      game_dir / 'data_sts2_linuxbsd_x86_64']
        data = next((item for item in candidates if (item / 'sts2.dll').is_file()), None)
    binaries = {}
    for filename in ('sts2.dll', 'GodotSharp.dll', '0Harmony.dll'):
        item = data / filename if data else None
        binaries[filename] = ({'bytes': item.stat().st_size, 'sha256': file_hash(item)}
                              if item and item.is_file() else None)
        if binaries[filename] is None:
            issues.append('missing ' + filename)
    ritsu = ritsu_dir / 'STS2-RitsuLib.dll' if ritsu_dir else None
    binaries['STS2-RitsuLib.dll'] = ({'bytes': ritsu.stat().st_size, 'sha256': file_hash(ritsu)}
                                   if ritsu and ritsu.is_file() else None)
    if binaries['STS2-RitsuLib.dll'] is None:
        issues.append('missing explicit RitsuLib binary; Steam workshop auto-discovery not performed')
    sources = None
    if upstream:
        try: sources = verify_upstream(upstream, allow_dirty=True)
        except ContractError as error: issues.append(str(error))
    else:
        issues.append('no local upstream checkout supplied')
    mods = {}
    if mods_dir and mods_dir.is_dir():
        for item in sorted(mods_dir.rglob('*')):
            if item.is_file() and item.suffix.lower() in ('.dll', '.json', '.pck'):
                mods[item.relative_to(mods_dir).as_posix()] = file_hash(item)
    return {'schema': 'spire-local-environment/v1', 'checks': checks,
            'game_data_dir': str(data.resolve()) if data else None,
            'game_version': 'not inferred; identify by binary SHA256', 'binaries': binaries,
            'upstream': sources, 'scanned_mods_sha256': mods,
            'mod_coverage': 'only explicitly supplied directory, not a claim of all active mods',
            'issues': issues, 'native_dependencies_found': not issues,
            'native_build_verified': False, 'native_game_equivalence_verified': False,
            'native_exact_adapter_implemented': False, 'network_used': False}


def build_native(path: Path, game_data: Path, ritsu: Path, output: Path) -> dict[str, Any]:
    provenance = verify_upstream(path, allow_dirty=True)
    for item in (game_data / 'sts2.dll', game_data / 'GodotSharp.dll', game_data / '0Harmony.dll', ritsu / 'STS2-RitsuLib.dll'):
        if not item.is_file(): raise ContractError('missing native dependency: ' + str(item))
    if not shutil.which('dotnet'): raise ContractError('.NET SDK not found')
    if output.exists() and any(output.iterdir()):
        raise ContractError('build output directory is not empty; use a new directory')
    output.mkdir(parents=True, exist_ok=True)
    properties = ['-p:CopyModOnBuild=false', f'-p:Sts2DataDir={game_data.resolve()}',
                  f'-p:RitsuLibDir={ritsu.resolve()}']
    commands = [['dotnet', 'build', 'CombatSolver.csproj', '-c', 'Release'] + properties,
                ['dotnet', 'build', 'tools/OfflineSearchHarness/OfflineSearchHarness.csproj', '-c', 'Release'] + properties]
    for i, command in enumerate(commands):
        text = run(command, path, timeout=600)
        (output / f'build-{i}.log').write_text(text+'\n', encoding='utf-8')
    result = {'provenance': provenance, 'commands': commands, 'build_completed': True,
              'native_game_equivalence_verified': False, 'installed_into_game': False}
    write_json(output / 'build.json', result)
    return result


def native_baseline(path: Path, output: Path, seed: str = '42', request: Path | None = None,
                    budget_ms: int = 30_000) -> dict[str, Any]:
    provenance = verify_upstream(path, allow_dirty=True)
    harness = path / 'tools/OfflineSearchHarness/bin/Release/net9.0/OfflineSearchHarness.dll'
    if not harness.is_file(): raise ContractError('build OfflineSearchHarness first; missing ' + str(harness))
    if output.exists() and any(output.iterdir()):
        raise ContractError('refusing to mix a baseline with existing output files')
    output.mkdir(parents=True, exist_ok=True)
    command = ['dotnet', str(harness.resolve()), '--out', str(output.resolve()), '--label', 'spireexact-baseline',
               '--seed', seed, '--profile', 'Low', '--search-mode', 'Evaluate', '--dop', '1',
               '--budget-ms', str(budget_ms)]
    if request:
        if not request.is_file(): raise ContractError('request file does not exist')
        command += ['--request', str(request.resolve())]
    record = {'provenance': provenance, 'command': command, 'algorithm': 'upstream Beam Search',
              'harness_sha256': file_hash(harness), 'proven_optimal': False,
              'native_exact_adapter_used': False,
              'native_game_equivalence_verified': False}
    write_json(output / 'baseline-invocation.json', record)
    try:
        log = run(command, path, timeout=max(180, budget_ms // 1000 + 120))
        (output / 'baseline-stdout.log').write_text(log+'\n', encoding='utf-8')
    except ContractError as error:
        record['status'] = 'FAILED'
        record['error'] = str(error)
        write_json(output / 'baseline-summary.json', record)
        raise
    result_file = output / 'harness-result.json'
    if not result_file.is_file(): raise ContractError('harness returned without its required result file')
    record['status'] = 'BASELINE_COMPLETED_NOT_EXACT'
    record['harness_result'] = json.loads(result_file.read_text(encoding='utf-8-sig'))
    (output / 'baseline-summary.json').write_text(json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    return record
